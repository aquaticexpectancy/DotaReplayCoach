"""AI coaching layer (xAI Grok).

Design rule, and the reason this isn't generic slop: the model receives ONLY
facts we computed deterministically — the movement diagnosis, the habit rollup,
who killed you with what, your role, your items, and the live-patch ability kits
of the heroes actually involved. It never sees raw positions and is never asked
"how did I play?". Its job is synthesis and hero-specific prescription.

Provider coupling is confined to `_call_grok()`. `build_context()` is pure
Python with no vendor dependency, so swapping backends is a one-function job.

Degrades cleanly to no AI section when the key is missing or the call fails.
"""
from __future__ import annotations
import json
import math
import os

import abilities as abilities_mod
import config as C
import dota_live
import heroes as heroes_mod
import items as items_mod
from diagnose import WINDOW

# Cost knobs, all overridable from .env:
#   COACH_MODEL   grok-4.5 (best) | grok-4.3 (~40% fewer output tokens, 3x faster)
#   COACH_EFFORT  low|high — note this mostly shifts reasoning into visible text
#                 rather than reducing total output; measure before trusting it.
#   COACH_VERBOSE_KITS=1 keeps ability flavour text (26% larger payload, no
#                 measured accuracy gain — the numbers and flags carry that).
MODEL = os.environ.get("COACH_MODEL", "grok-4.5")
EFFORT = os.environ.get("COACH_EFFORT") or None
LEAN_KITS = os.environ.get("COACH_VERBOSE_KITS", "") != "1"
TIMEOUT = 300
WORLD_PER_GRID = 128

_LANE = {1: "safe lane", 2: "mid", 3: "off lane", 4: "jungle/roam"}

SYSTEM = """You are a Dota 2 coach reviewing one player's whole match — what they did well
as much as what went wrong.

You are given FACTS extracted from the replay — not raw data to analyze. Your job
is to turn them into an assessment that player could not have written themselves.

Rules:
- Credit is as important as criticism. `match.percentiles_vs_same_hero` are real
  percentiles against other players on the SAME hero — use them by name and
  number. A 96th-percentile figure is genuinely elite; say so.
- Never state a number, timing, percentile or count that is not in the data.
  Do not estimate. If you want to say "X% of players", it must come from
  `percentiles_vs_same_hero`.
- Judge itemisation against the enemy heroes actually present and their real
  ability kits, using the real purchase timings in `match.build_order`. Compare
  those timings to `meta_reference.early_core_with_win_rates` — if they bought
  something notably later or earlier than the typical minute, say so with both
  numbers. Cite win rates only from that block; never invent one.
- BEFORE recommending any item, check it against `you.attack_type` and
  `meta_reference.commonly_built_by_phase`. Never suggest an item whose value is
  attack-range-based (Hurricane Pike, Dragon Lance) for a Melee hero — the range
  bonus does nothing for them. If you recommend something that does not appear in
  the commonly-built lists, you must justify why this specific game warrants an
  off-meta buy; otherwise prefer an item the hero actually builds.
- `meta_reference.this_hero_win_rate_vs_each_enemy` is this hero's measured win
  rate against each enemy hero. Entries with `reliable: false` come from a small
  sample — you may mention them only as weak evidence, and never build the
  assessment on them. Do not claim a draft was favourable or unfavourable
  without this data.
- `match.deaths` is the complete death list — do not miscount them or claim a
  death happened in a phase it didn't.
- Never restate what happened. They watched the game. "You died to a gank" is worthless.
- Every recommendation must be specific to THIS hero, THIS role, and THE enemies named.
  Reference actual ability names, item timings, and ranges. "Ward more" and "play safer"
  are failures.
- An `awareness` diagnosis means they never reacted — the fix is information (what to
  watch, what to infer). An `execution` diagnosis means they saw it and still died — the
  fix is an earlier exit, a mobility/save item, or not taking the fight.
- A `committed` diagnosis is a teamfight; judge fight selection and positioning, not awareness.
- If an item they owned would have saved them, say so plainly. If they lacked one, name it
  and say when to buy it.
- Never invent facts. If something isn't in the data, don't assert it.
- BKB and dispel claims must come from the ability's own `pierces_bkb` and
  `dispellable` fields — never from intuition. If `pierces_bkb` is "Yes", BKB
  does NOT stop that ability and you must not say it does; recommend a different
  answer (positioning, a save, status resistance, killing the caster first).
- Titles: `game_phase` and `displaced_by` are binding. During `laning` a death is a
  lane fight or a pull, never a "gank". If `displaced_by` is set the player was
  physically moved — they did not walk into anything. If it is null, do not
  imply they were hooked/grabbed.
- DEFENSIVE ITEMS MUST MATCH THE DAMAGE THAT ACTUALLY LANDED.
  `damage_taken_profile` is measured: it lists what did the damage, of which
  type, and which enemy dealt the most. Before recommending any defensive item,
  read it. Recommend BKB, Pipe or Hood only when magical damage is a real share
  of what killed them, and JUSTIFY IT WITH THE ABILITIES THAT ACTUALLY LANDED —
  name them from that list. Recommend armour or evasion when the damage was
  mostly physical. Never recommend a defensive item against an ability whose
  `pierces_bkb` is "Yes": that item does not stop it, and saying so is a
  contradiction. If the biggest threat pierces BKB, the answer is positioning,
  a dispel, a save from a teammate, or killing the caster first.
- Rank threats by what they cost the player, not by what sounds scary. A
  channelled AoE ultimate that actually hit them matters more than a utility
  steal. Do not build advice around an ability that appears nowhere in the
  damage profile and never displaced them.
- Say what to WATCH FOR, not just what to buy. The most useful line names the
  enemy, the ability, and the situation to avoid: "Visage had Orchid — do not
  get caught alone without a dispel" beats any item list.
- Be concise. Every sentence must change what they do next game.

VOICE — this matters as much as the facts:
- Talk like a coach sitting next to them, not like a stats page. Short sentences,
  plain words, second person.
- You were given far more data than belongs in the answer. Most of it exists so
  you can check yourself, NOT so you can repeat it. Selecting what to leave out
  is the job.
- Hard limit: at most ONE number per sentence, and only when the number is the
  point. Never chain stats ("496 GPM, 96th percentile, 14.8k damage, 2.1k tower
  damage") — that reads as noise and the player stops reading. Pick the one that
  proves your claim and drop the rest.
- Do not name the source of a fact ("per the benchmark data", "the meta reference
  shows"). Just assert it. The player does not care where it came from.
- Lead with the judgement, then the evidence. "Your farm was never the problem —
  496 GPM is top 4% on Brood" beats a stat dump followed by a verdict.
- Before you answer, silently ask: which two things, if they changed, would most
  change this player's next game? Write about those. Everything else is cut."""


def _lane_name(track) -> str:
    if getattr(track, "is_roaming", False):
        return "roaming"
    return _LANE.get(getattr(track, "lane_role", None), "unknown role")


def _hero_kit(hero_id: int, ab_meta: dict, hero_abs: dict, full: bool = False):
    """Hero's kit with live-patch numbers from Valve's datafeed.

    `full=True` (the player, and whoever killed them) returns real cooldowns,
    mana costs, cast ranges, damage and talents — the numbers a coach actually
    cites. Falls back to bare ability names from OpenDota if Valve is
    unreachable, so the coach still works offline-ish.
    """
    live = dota_live.hero_profile(hero_id)
    if live.get("abilities"):
        if not full:
            return [a["name"] for a in live["abilities"]]
        abilities = live["abilities"]
        if LEAN_KITS:
            # Drop flavour prose, keep every number and interaction flag — those
            # are what the advice is actually checked against.
            abilities = [{k: v for k, v in a.items() if k != "desc"}
                         for a in abilities]
        out = {"abilities": abilities}
        if live.get("talents"):
            out["talents"] = live["talents"]
        if live.get("facets"):
            out["facets"] = live["facets"]
        return out

    names = []
    for aid in (hero_abs.get(str(hero_id)) or []):
        info = abilities_mod.info(aid, ab_meta)
        if info and not abilities_mod.is_noise(aid, ab_meta):
            names.append(info["name"])
    return names[:6] if not full else {"abilities": names[:6]}


def _damage_taken(me, match, ab_meta: dict, hname) -> dict:
    """Measured: what actually damaged this player, and of what type.

    Whole-match totals from OpenDota (`damage_inflictor_received`), not per
    death. This exists so defensive-item advice is checked against damage that
    actually landed instead of against whichever enemy ability sounds scariest.
    """
    by_key = items_mod.load_by_key()
    ab_by_key = {(m or {}).get("key"): int(aid)
                 for aid, m in (ab_meta or {}).items() if (m or {}).get("key")}
    flags = dota_live._interaction_flags()

    sources, split = [], {}
    for key, amount in (getattr(me, "dmg_received", {}) or {}).items():
        if not amount:
            continue
        if key == "null":
            name, dt = "right-click attacks", "physical"
        elif key in ab_by_key:
            name = abilities_mod.info(ab_by_key[key], ab_meta).get("name") or key
            dt = (flags.get(key) or {}).get("damage_type") or "unknown"
        elif key in by_key:
            name = (by_key[key] or {}).get("name") or key
            dt = (flags.get(key) or {}).get("damage_type") or "unknown"
        else:
            continue
        dt = str(dt).lower()
        entry = {"source": name, "damage": int(amount), "damage_type": dt}
        pb = (flags.get(key) or {}).get("pierces_bkb")
        if pb is not None:
            entry["pierces_bkb"] = pb
        sources.append(entry)
        split[dt] = split.get(dt, 0) + int(amount)

    sources.sort(key=lambda r: -r["damage"])
    total = sum(split.values()) or 1
    per_hero = {}
    for unit, amount in (getattr(me, "dmg_by_unit", {}) or {}).items():
        if unit.startswith("npc_dota_hero_") and amount:
            per_hero[unit.replace("npc_dota_hero_", "")] = int(amount)
    return {
        "note": "Whole-match totals, not per death.",
        "top_sources": sources[:8],
        "share_by_damage_type_pct": {k: round(100 * v / total)
                                     for k, v in sorted(split.items(), key=lambda kv: -kv[1])},
        "damage_dealt_to_you_by_enemy": dict(sorted(per_hero.items(),
                                                    key=lambda kv: -kv[1])[:5]),
        "times_each_enemy_killed_you": {
            k.replace("npc_dota_hero_", ""): v
            for k, v in (getattr(me, "killed_by", {}) or {}).items()},
    }


def build_context(match, me, analyses, diags, habit, match_summary=None) -> dict:
    """Compact, factual payload. No raw positions — computed findings only."""
    hero = heroes_mod.load()
    ab_meta = abilities_mod.load_meta()
    ults = abilities_mod.load_ultimates()
    item_db = items_mod.load()
    hname = lambda h: hero.get(str(h), {}).get("name", f"hero {h}")

    # Only the heroes that actually mattered: the player + whoever killed them.
    killer_ids = set()
    for a in analyses:
        if a.death and a.death.killer:
            killer_ids.add(a.death.killer)
        if a.death:
            killer_ids.update(a.death.assists or [])

    hero_abs = {}
    try:
        import json as _json, os as _os
        p = _os.path.join(_os.path.dirname(__file__), "hero_abilities_by_id.json")
        if _os.path.exists(p):
            hero_abs = _json.load(open(p, encoding="utf-8"))
    except Exception:
        hero_abs = {}

    deaths = []
    for a in sorted(analyses, key=lambda x: x.time):
        d, f = a.death, a.features
        dg = diags.get(a.index) or {}
        inv = items_mod.snapshot_items(me.purchase_log, a.time) if me.purchase_log else []
        deaths.append({
            "n": a.index + 1,
            "clock": f"{int(a.time // 60)}:{int(a.time % 60):02d}",
            "label": a.label,
            "diagnosis": dg.get("kind"),
            "diagnosis_detail": dg.get("title"),
            "reaction_seconds_before_death": dg.get("reaction"),
            "was_low_hp_before": dg.get("lowBefore"),
            "killed_by": hname(d.killer) if d and d.killer else "creeps/tower",
            "assisted_by": [hname(x) for x in (d.assists or [])] if d else [],
            "gold_lost": int((d.gold_lost if d else 0) or 0),
            "game_phase": "laning" if f.get("laning") else "post-laning",
            # Set only when an ability physically dragged them (hook, skewer…).
            "displaced_by": f.get("displaced_by"),
            # None means "no ally was alive", not "distance unknown". Infinity
            # is what the detector uses for that, and it cannot be rounded.
            "nearest_ally_units": (
                round(f["nearest_ally"] * WORLD_PER_GRID)
                if math.isfinite(f.get("nearest_ally", float("inf"))) else None),
            "all_allies_dead": not math.isfinite(f.get("nearest_ally", 0.0)),
            "enemies_within_1500": f.get("enemies_near"),
            # Enemies who were ON them at death but far 8s before — i.e. actually
            # rotated in. Not a count of distant enemies elsewhere on the map.
            "enemies_that_rotated_in": f.get("gankers_were_far"),
            "had_observer_vision": f.get("warded"),
            "on_enemy_half": f.get("in_enemy_half"),
            "in_enemy_tower_range": f.get("near_enemy_tower"),
            "your_items_then": [i["name"] for i in inv],
        })

    # Full live-patch kits only for the heroes that actually killed them —
    # keeps the payload focused and the token cost down.
    enemy_kits = {}
    for hid in sorted(killer_ids):
        kit = _hero_kit(hid, ab_meta, hero_abs, full=True)
        kit["ultimate"] = (ults.get(str(hid)) or {}).get("name")
        enemy_kits[hname(hid)] = kit

    # Measured meta: what this hero actually builds in this role, and how the
    # hero historically fares against each enemy. Turns itemisation advice from
    # opinion into a comparison against real win rates.
    try:
        import meta as meta_mod
        meta_items = meta_mod.hero_items(me.hero_id, getattr(me, "lane_role", None))
        meta_matchups = meta_mod.matchups(
            me.hero_id, [h.hero_id for h in match.enemies_of(me)], hname)
        meta_popular = meta_mod.item_popularity(me.hero_id)
    except Exception:
        meta_items, meta_matchups, meta_popular = [], [], {}
    me_hero = hero.get(str(me.hero_id), {})

    return {
        "match": match_summary or {},
        "meta_reference": {
            "note": "Measured from public match data. `typical_purchase` is the "
                    "average minute other players on this hero+role buy the item; "
                    "compare it against match.build_order to judge timings.",
            "early_core_with_win_rates": meta_items,
            "commonly_built_by_phase": meta_popular,
            "this_hero_win_rate_vs_each_enemy": meta_matchups,
        },
        "you": {
            "hero": hname(me.hero_id),
            "attack_type": me_hero.get("attack_type"),   # Melee | Ranged
            "primary_attr": me_hero.get("primary_attr"),
            "role": _lane_name(me),
            "side": "Radiant" if me.is_radiant else "Dire",
            "kit": _hero_kit(me.hero_id, ab_meta, hero_abs, full=True),
            "ultimate": (ults.get(str(me.hero_id)) or {}).get("name"),
            "final_build": [i["name"] for i in
                            items_mod.from_item_ids(me.final_items or [], item_db)],
        },
        "enemies_who_killed_you": enemy_kits,
        "match_pattern": {
            "headline": habit["headline"]["title"],
            "detail": habit["headline"].get("text"),
            "nemesis": habit.get("nemesis"),
            "all_patterns": [{"pattern": p["title"], "count": p["count"],
                              "of": p["total"]} for p in habit.get("patterns", [])],
        },
        "damage_taken_profile": _damage_taken(me, match, ab_meta, hname),
        "deaths": deaths,
        "notes": {
            "movement_window_seconds": WINDOW,
            "isolated_threshold_units": round(C.ISOLATED_ALLY * WORLD_PER_GRID),
        },
    }


try:                                    # schema doubles as the response contract
    from pydantic import BaseModel, Field

    class _DeathAdvice(BaseModel):
        n: int = Field(description="The death number being advised on.")
        title: str = Field(
            description="A short, specific headline for this death (max ~7 words). "
                        "Name the actual mechanic and context — 'Hooked through the "
                        "trees while last-hitting', 'Dove a 2v1 with no escape'. "
                        "It must be consistent with the facts given: do not call a "
                        "laning-stage lane fight a gank, and do not mention a "
                        "displacement unless displaced_by is set.")
        advice: str = Field(
            description="What to do differently at this exact moment. Name "
                        "abilities, items and ranges. Never restate what happened.")

    class _Point(BaseModel):
        title: str = Field(description="Short headline, max ~6 words.")
        detail: str = Field(
            description="ONE or TWO sentences, max ~35 words total. Carry at "
                        "most one number — the single most telling one. Say what "
                        "it means, not what it is. No lists of stats.")

    class _Coaching(BaseModel):
        overall: str = Field(
            description="2-3 sentences, max ~55 words. The story of the game in "
                        "plain language: what actually decided it for this player. "
                        "At most two numbers in the whole paragraph.")
        did_well: list[_Point] = Field(
            description="Exactly 2 genuine strengths — the two biggest, not a "
                        "catalogue. Each backed by one real number.")
        mistakes: list[_Point] = Field(
            description="Exactly 2 real errors, each with what to do instead. "
                        "Only things the data supports.")
        itemization: str = Field(
            description="2-3 sentences, max ~55 words. Name the single most "
                        "important build change and why, against a named enemy "
                        "ability. Do not walk through the whole build.")
        headline: str = Field(
            description="One sentence naming the player's single most costly habit "
                        "this match, specific to their hero and role.")
        fix: str = Field(
            description="The concrete behavioural change that addresses the "
                        "headline. Hero- and role-specific.")
        drill: str = Field(
            description="One testable thing to do next game, phrased so they can "
                        "tell afterwards whether they actually did it.")
        deaths: list[_DeathAdvice] = Field(
            description="One entry per death provided, in the same order.")
except ImportError:                     # pydantic ships with the SDK; guard anyway
    _Coaching = None


def _call_grok(context: dict, log) -> dict | None:
    key = os.environ.get("XAI_API_KEY")
    if not key:
        log("  (XAI_API_KEY not set — skipping AI coaching)")
        return None
    if _Coaching is None:
        log("  (pydantic unavailable — skipping AI coaching)")
        return None

    try:
        from xai_sdk import Client
        from xai_sdk.chat import system, user
    except ImportError:
        log("  (xai_sdk not installed — run: pip install xai-sdk)")
        return None

    kwargs = {"model": MODEL}
    if EFFORT:
        kwargs["reasoning_effort"] = EFFORT
    try:
        chat = Client(api_key=key, timeout=TIMEOUT).chat.create(**kwargs)
        chat.append(system(SYSTEM))
        chat.append(user("Coach this player.\n\n" + json.dumps(context, indent=1)))
        # parse() derives the JSON schema from the model and returns it typed,
        # so there's no hand-written schema and no response parsing to get wrong.
        response, advice = chat.parse(_Coaching)
    except Exception as e:
        log(f"  (AI coaching unavailable: {e})")
        return None

    if advice is None:
        log("  (AI coaching returned nothing)")
        return None

    u = getattr(response, "usage", None)
    if u is not None:
        rt = getattr(u, "reasoning_tokens", None)
        log(f"  Coaching ready ({u.prompt_tokens} in / {u.completion_tokens} out"
            + (f", {rt} reasoning" if rt else "") + f", {MODEL})")
    return advice.model_dump()


def advise(context: dict, log=print) -> dict | None:
    """Ask Grok for targeted coaching. Returns None if unavailable."""
    return _call_grok(context, log)
