"""Single-page interactive match report: every death, one map, full viewport.

Python precomputes everything (marker px coords, trails, wards, building status,
hp/mana/level/items at each death) into one JSON blob; a small inline script
switches the map overlay + detail panel when a death is selected.
Everything drawn is recorded data — no AI speculation.
"""
from __future__ import annotations
import base64
import functools
import html
import json
import math
import os
import config as C
import heroes
import items as items_mod
import dota_live
import abilities as abilities_mod
import status as status_mod
import diagnose as diagnose_mod
from detect_deaths import DeathAnalysis
from models import MatchData, HeroTrack
from positions import alive, pos_at, hp_at, level_at, items_at

M = 640                       # svg viewBox size (scales to viewport)
_ASSETS = os.path.join(os.path.dirname(__file__), "assets")
WORLD_PER_GRID = 128
_TP_JUMP = 20.0
STATE_LOOKBACK = 3.0          # hp/mana sampled this many seconds before death
PLAYBACK_BEFORE = 10.0        # seconds of lead-in before death
PLAYBACK_STEP = 0.5           # frame step for the scrubber
CAST_LOOKBACK = 10.0          # ability/item uses shown before death

# Plain-English overrides for detector labels (detector keeps short keys).
_LABEL_COPY = {
    "Hooked out of position": {
        "title": "Hooked out of position",
        "blurb": "A hook landed and dragged you in — you did not walk into this.",
        "tip": "Stand behind creeps or terrain that blocks the hook line when a hooker has vision on you.",
    },
    "Dove enemy tower": {
        "title": "Dove under their tower",
        "blurb": "You died inside enemy tower range while enemies were on you.",
        "tip": "Only dive when the kill is free or your team is already committing with you.",
    },
    "Pushed into a gank (no info)": {
        "title": "Walked into an unseen gank",
        "blurb": "Enemies rotated onto you from far away, and you had little information.",
        "tip": "If the fog is dark and you are deep, assume someone is coming — ward first or leave.",
    },
    "Overextended pickoff": {
        "title": "Caught alone too deep",
        "blurb": "You were isolated on their side of the map when they collapsed.",
        "tip": "Stay near allies (or a safe escape) when you cross the river.",
    },
    "Caught with no vision": {
        "title": "Caught without vision",
        "blurb": "You were alone and the area was not covered by your Observer wards.",
        "tip": "Ask for a ward, or play closer to lit ground and your team.",
    },
    "Lost teamfight": {
        "title": "Lost the teamfight",
        "blurb": "Several enemies and allies were nearby — this looks like a fight, not a solo mistake.",
        "tip": "Review positioning and spell timing in the fight rather than blaming the death alone.",
    },
    "Death": {
        "title": "Died in a hard spot",
        "blurb": "The data does not point to one clear solo mistake — still useful to review.",
        "tip": "Check whether you could have left earlier, or waited for allies / vision.",
    },
}


def P(x: float, y: float) -> tuple[float, float]:
    span = C.GAME_MAX - C.GAME_MIN
    return (round((x - C.GAME_MIN) / span * M, 1),
            round((1.0 - (y - C.GAME_MIN) / span) * M, 1))


@functools.lru_cache(maxsize=32)
def _b64(fname: str) -> str:
    mime = "image/jpeg" if fname.lower().endswith((".jpg", ".jpeg")) else "image/png"
    with open(os.path.join(_ASSETS, fname), "rb") as fh:
        return f"data:{mime};base64," + base64.b64encode(fh.read()).decode()


def _trail_segments(track: HeroTrack, t0: float, t1: float) -> list[list[list[float]]]:
    pts = [p for p in track.positions if t0 <= p.time <= t1]
    segs, cur, prev = [], [], None
    for p in pts:
        if prev is not None and math.hypot(p.x - prev.x, p.y - prev.y) > _TP_JUMP:
            if len(cur) > 1:
                segs.append(cur)
            cur = []
        cur.append(list(P(p.x, p.y)))
        prev = p
    if len(cur) > 1:
        segs.append(cur)
    return segs


def _wu(grid: float) -> str:
    return f"{grid * WORLD_PER_GRID:.0f}"


def _severity(score: float, label: str) -> dict:
    """Map detector score to user-facing severity for the UI.

    Rank is only for presentation badges — list order / focus use `score`.
    Teamfights are labeled separately so they do not look like solo mistakes.
    """
    if label == "Lost teamfight":
        return {"key": "fight", "label": "Teamfight", "rank": 0}
    if score >= 4.0:
        return {"key": "critical", "label": "Key mistake", "rank": 3}
    if score >= 2.0:
        return {"key": "notable", "label": "Worth reviewing", "rank": 2}
    return {"key": "normal", "label": "Minor", "rank": 1}


def _findings(f: dict) -> list[dict]:
    """Structured findings: short title + plain sentence (easy to scan)."""
    out: list[dict] = []
    if not math.isfinite(f["nearest_ally"]):
        # No living ally anywhere — the detector's "distance" is infinite, and
        # printing that as a number gives "about inf units away".
        out.append({
            "tone": "bad",
            "title": "Alone",
            "text": "Every ally was dead — there was no help on the map at all.",
        })
    elif f["nearest_ally"] > C.ISOLATED_ALLY:
        out.append({
            "tone": "bad",
            "title": "Alone",
            "text": f"Nearest ally was about {_wu(f['nearest_ally'])} units away — you had no backup.",
        })
    elif f["nearest_ally"] < 10:
        out.append({
            "tone": "ok",
            "title": "Fight nearby",
            "text": f"Allies were close (~{_wu(f['nearest_ally'])} units). This reads as a fight, not a lonely pickoff.",
        })
    else:
        out.append({
            "tone": "warn",
            "title": "Support far",
            "text": f"Nearest ally was about {_wu(f['nearest_ally'])} units away — help was slow to reach you.",
        })

    if f["enemies_near"] >= 2:
        out.append({
            "tone": "bad",
            "title": f"{f['enemies_near']} enemies close",
            "text": "Multiple enemies were within roughly 1500 units when you died.",
        })
    elif f["enemies_near"] == 1:
        out.append({
            "tone": "warn",
            "title": "One enemy close",
            "text": "At least one enemy was in threaten range when you went down.",
        })

    if f.get("displaced_by"):
        out.append({
            "tone": "bad",
            "title": f"{f['displaced_by']}",
            "text": ("You were physically pulled out of position — this was not a "
                     "movement decision, it was a landed ability."),
        })
    if f["gankers_were_far"] >= 2:
        out.append({
            "tone": "bad",
            "title": "Unseen rotation",
            "text": (f"{f['gankers_were_far']} of the enemies on you had been far "
                     f"away {C.ROTATE_LOOKBACK:.0f}s earlier — they rotated in."),
        })

    if f["warded"]:
        out.append({
            "tone": "ok",
            "title": "Area was warded",
            "text": "An allied Observer covered this spot — the information was available.",
        })
    else:
        out.append({
            "tone": "warn",
            "title": "No ward cover",
            "text": "No allied Observer ward covered this area when you died.",
        })

    if f["in_enemy_half"]:
        out.append({
            "tone": "warn",
            "title": "Enemy side",
            "text": "This happened on their half of the map — risk was already high.",
        })
    if f["near_enemy_tower"]:
        out.append({
            "tone": "bad",
            "title": "Under their tower",
            "text": "You were inside enemy tower range.",
        })
    if f.get("enemies_dead"):
        n = f["enemies_dead"]
        out.append({
            "tone": "ok",
            "title": "Numbers advantage",
            "text": (f"{n} enem{'ies were' if n != 1 else 'y was'} already dead "
                     "(not shown on the map)."),
        })
    return out


def _chips(d) -> list[dict]:
    if not d:
        return []
    out = []
    if d.gold_lost:
        out.append({"k": "gold", "t": f"Lost {d.gold_lost:.0f} gold"})
    if d.time_dead:
        out.append({"k": "time", "t": f"Respawn {d.time_dead:.0f}s"})
    fl = d.flags
    if fl.get("isBurst"):
        out.append({"k": "burst", "t": "Burst down"})
    if fl.get("hasHealAvailable"):
        out.append({"k": "heal", "t": "Heal unused"})
    if fl.get("isAttemptTpOut"):
        out.append({"k": "tp", "t": "Tried to TP"})
    if fl.get("isDieBack"):
        out.append({"k": "back", "t": "Died backing"})
    if fl.get("isFeed"):
        out.append({"k": "feed", "t": "Feed death"})
    return out


def _econ_at(track: HeroTrack, t: float) -> tuple[int | None, int | None]:
    """Cumulative (last_hits, gold) at or before t from OpenDota minute series."""
    if not track.econ_times or not track.lh_t:
        return None, None
    idx = None
    for i, tt in enumerate(track.econ_times):
        if tt <= t:
            idx = i
        else:
            break
    if idx is None:
        return 0, 0
    lh = track.lh_t[idx] if idx < len(track.lh_t) else track.lh_t[-1]
    gold = track.gold_t[idx] if idx < len(track.gold_t) else (
        track.gold_t[-1] if track.gold_t else 0)
    return int(lh or 0), int(gold or 0)


def _farm_finding(track: HeroTrack, t: float) -> dict | None:
    """Did you look like you were farming in the minutes before death?"""
    lh_now, gold_now = _econ_at(track, t)
    lh_prev, gold_prev = _econ_at(track, t - 120)
    if lh_now is None or lh_prev is None:
        return None
    lh_delta = max(0, lh_now - lh_prev)
    gold_delta = max(0, (gold_now or 0) - (gold_prev or 0))
    if lh_delta >= 10:
        return {
            "tone": "ok",
            "title": "Was farming",
            "text": (f"About {lh_delta} last hits (+{gold_delta} gold) in the 2 minutes "
                     "before this death — you were gathering farm."),
        }
    if lh_delta <= 2:
        return {
            "tone": "warn",
            "title": "Not farming",
            "text": (f"Only {lh_delta} last hit{'s' if lh_delta != 1 else ''} "
                     f"(+{gold_delta} gold) in the 2 minutes before — more fighting/moving than farming."),
        }
    return {
        "tone": "warn",
        "title": "Light farm",
        "text": (f"About {lh_delta} last hits (+{gold_delta} gold) in the 2 minutes before — "
                 "some farm, but not a full farming pattern."),
    }


def _story(f: dict, farm: dict | None) -> str:
    """Short lead-up story so the label makes sense without watching the clip."""
    if f.get("displaced_by"):
        base = (f"Lead-up ends with a landed {f['displaced_by'].lower()} — you were "
                "moved, so judge the positioning that made you catchable.")
    elif f.get("laning") and f.get("enemies_near", 0) <= 2:
        base = "Lead-up looks like a lane fight rather than a rotation."
    elif f.get("gankers_were_far", 0) >= 2 and f.get("enemies_near", 0) >= 2:
        base = "Lead-up looks like a gank: enemies closed from far away."
    elif f.get("enemies_near", 0) >= 3 and f.get("nearest_ally", 99) < 10:
        base = "Lead-up looks like a teamfight: both sides were already stacked."
    elif f.get("near_enemy_tower"):
        base = "Lead-up looks like a dive: you were under their tower when it ended."
    elif f.get("nearest_ally", 0) > C.ISOLATED_ALLY and f.get("in_enemy_half"):
        base = "Lead-up looks like an overextension: alone and deep when they found you."
    elif f.get("enemies_near", 0) >= 1 and f.get("nearest_ally", 99) < 12:
        base = "Lead-up looks like a trade/skirmish: allies and enemies were both close."
    else:
        base = "Lead-up is mixed — use the 10s replay below to see how bodies moved."
    if farm and farm["title"] == "Was farming":
        base += " You had been farming just before."
    elif farm and farm["title"] == "Not farming":
        base += " You were not farming in the moments before."
    return base


def _copy_for(label: str) -> dict:
    if label in _LABEL_COPY:
        return _LABEL_COPY[label]
    if label.endswith("out of position"):        # Skewered / Lassoed / Tossed…
        verb = label.split(" out of")[0]
        return {
            "title": label,
            "blurb": f"{verb} — you were physically moved, not out-positioned by "
                     "your own movement.",
            "tip": "Break the spacing or line of sight that made the cast possible; "
                   "once it lands the death is usually already decided.",
        }
    return _LABEL_COPY["Death"]


def _ult_status(track: HeroTrack, t: float, ab_meta: dict, ults: dict) -> dict | None:
    """Was this hero's ultimate ready at time t?"""
    ult = ults.get(str(track.hero_id))
    if not ult:
        return None
    aid = ult["abilityId"]
    lvl = level_at(track, t) or 1
    name = ult.get("name") or abilities_mod.info(aid, ab_meta)["name"]
    icon = abilities_mod.info(aid, ab_meta).get("icon")
    if not abilities_mod.ult_rank_available(lvl):
        return {
            "name": name, "icon": icon, "abilityId": aid,
            "ready": False, "state": "unskilled",
            "text": f"{name} not skilled yet (hero level {lvl}).",
            "remaining": None, "lastCast": None,
        }
    cd = abilities_mod.cooldown(aid, lvl, ab_meta)
    last = None
    for ct, ca, _ in track.ability_casts:
        if ca == aid and ct <= t:
            last = ct
    if last is None:
        return {
            "name": name, "icon": icon, "abilityId": aid,
            "ready": True, "state": "ready",
            "text": f"{name} was ready (not used yet this game, or not in the log).",
            "remaining": 0, "lastCast": None,
        }
    if cd is None:
        return {
            "name": name, "icon": icon, "abilityId": aid,
            "ready": None, "state": "unknown",
            "text": f"{name} last used at {int(last//60)}:{int(last%60):02d}.",
            "remaining": None, "lastCast": last,
        }
    rem = max(0.0, cd - (t - last))
    ready = rem <= 0.05
    return {
        "name": name, "icon": icon, "abilityId": aid,
        "ready": ready,
        "state": "ready" if ready else "cooldown",
        "text": (f"{name} was ready." if ready else
                 f"{name} on cooldown — ~{rem:.0f}s left "
                 f"(used at {int(last//60)}:{int(last%60):02d})."),
        "remaining": round(rem, 1), "lastCast": last, "cooldown": cd,
    }


_DMG_COLOUR = {"magical": "#60a5fa", "physical": "#fbbf24",
               "pure": "#f472b6", "unknown": "#94a3b8"}


def _damage_profile(me: HeroTrack, match: MatchData, ab_meta: dict,
                    item_db: dict, hmeta) -> dict:
    """What actually did the damage to this player, and of what type.

    WHOLE-MATCH totals. OpenDota reports damage by inflictor for the match, not
    per death, so this is deliberately presented as a match-level pattern and
    never pinned to one death's clock.
    """
    by_key = items_mod.load_by_key()
    ab_by_key = {}
    for aid, meta in (ab_meta or {}).items():
        k = (meta or {}).get("key")
        if k:
            ab_by_key[k] = int(aid)

    # Damage type per ability comes from the same flags file the coach reads.
    flags = dota_live._interaction_flags()

    rows = []
    for key, amount in (getattr(me, "dmg_received", {}) or {}).items():
        if not amount or key in ("null", None):
            continue                       # "null" is plain right-click damage
        aid = ab_by_key.get(key)
        item = by_key.get(key)
        if aid is not None:
            name = abilities_mod.info(aid, ab_meta).get("name") or key
            icon = abilities_mod.info(aid, ab_meta).get("icon")
            kind = "ability"
        elif item:
            name = item.get("name") or key.replace("_", " ").title()
            icon = item.get("icon")
            kind = "item"
        else:
            continue                       # unresolved engine name — don't guess
        dt = (flags.get(key) or {}).get("damage_type") or "unknown"
        rows.append({"name": name, "icon": icon, "kind": kind,
                     "damage": int(amount), "type": dt.lower()})
    rows.sort(key=lambda r: -r["damage"])

    auto = int((getattr(me, "dmg_received", {}) or {}).get("null") or 0)
    if auto:
        rows.insert(0, {"name": "Right-click attacks", "icon": None,
                        "kind": "attack", "damage": auto, "type": "physical"})

    # Which enemy hero hurt you most, by unit rather than by ability.
    per_hero = []
    for unit, amount in (getattr(me, "dmg_by_unit", {}) or {}).items():
        if not unit.startswith("npc_dota_hero_") or not amount:
            continue
        for h in match.enemies_of(me):
            nm, ic = hmeta(h.hero_id)
            if unit.endswith(nm.lower().replace(" ", "_").replace("'", "")):
                per_hero.append({"name": nm, "icon": ic, "damage": int(amount),
                                 "kills": int((getattr(me, "killed_by", {}) or {})
                                              .get(unit) or 0)})
                break
    per_hero.sort(key=lambda r: -r["damage"])

    total = sum(r["damage"] for r in rows) or 1
    split = {}
    for r in rows:
        split[r["type"]] = split.get(r["type"], 0) + r["damage"]
    return {
        "sources": rows[:8],
        "heroes": per_hero[:5],
        "total": total,
        "split": [{"type": k, "damage": v, "pct": round(100 * v / total)}
                  for k, v in sorted(split.items(), key=lambda kv: -kv[1])],
    }


def _positions(team: list[HeroTrack]) -> dict[int, int]:
    """hero_id -> role position 1..5 for one team.

    Dota has no "position" field anywhere; it is inferred. OpenDota gives
    `lane_role` (1 safe, 2 mid, 3 off, 4 jungle) and `is_roaming`, which fixes
    the lane but not who in that lane was the core. Farm priority settles it:
    within a lane the higher-GPM player is the core, the other is the support.
    """
    def gpm(h):
        return (getattr(h, "stats", {}) or {}).get("gpm") or 0

    def lane(h):
        if getattr(h, "is_roaming", False):
            return 4                       # roamers are supports, never cores
        return getattr(h, "lane_role", None)

    out: dict[int, int] = {}
    pool = sorted(team, key=gpm, reverse=True)
    supports: list = []

    for want_lane, core_pos in ((2, 2), (1, 1), (3, 3)):
        inlane = [h for h in pool if lane(h) == want_lane and h.hero_id not in out]
        if inlane:
            out[inlane[0].hero_id] = core_pos   # highest GPM in the lane
            supports.extend(inlane[1:])         # anyone else there is the support
    # Jungle, roaming and unknown-lane players fall through to the support pool.
    supports.extend(h for h in pool if h.hero_id not in out
                    and h not in supports)
    supports.sort(key=gpm, reverse=True)

    free = [p for p in (1, 2, 3, 4, 5) if p not in out.values()]
    for h, p in zip(supports, free):
        out[h.hero_id] = p
    return out


def _kit_of(hero_id: int, ab_meta: dict, hero_abs: dict) -> list[dict]:
    """Static ability list for a hero — name, icon and how it is unlocked.

    Valve's datafeed is the authority on what is actually in a hero's kit.
    The id list also carries engine sub-abilities and placeholders — Rubick
    alone contributes "Stolen Spell" twice, "Telekinesis Land" twice and two
    "Rubick Hidden" entries, which is what overflowed his row with unnamed
    skills. Anything Valve does not list is not a real ability, so drop it.
    """
    live_list = dota_live.hero_profile(hero_id).get("abilities") or []
    live = {a.get("abilityId"): a for a in live_list if a.get("abilityId")}
    out = []
    for aid in (hero_abs.get(str(hero_id)) or []):
        if abilities_mod.is_noise(aid, ab_meta):
            continue
        lv = live.get(aid)
        if live and lv is None:
            continue                      # not part of the real kit
        lv = lv or {}
        if lv.get("innate") or lv.get("passive"):
            continue                      # no cooldown to show
        nfo = abilities_mod.info(aid, ab_meta)
        name = lv.get("name") or nfo.get("name") or ""
        if not name or name.lower().startswith("ability "):
            continue                      # unresolved id — never show a number
        out.append({
            "id": aid, "name": name, "icon": nfo.get("icon"),
            "ult": bool(lv.get("is_ult")),
            "req": "scepter" if lv.get("needs_scepter")
                   else "shard" if lv.get("needs_shard") else None,
        })
    return out


def _ult_is_passive(hero_id: int, ults: dict) -> bool:
    """Tiny's Grow is an ultimate but cannot be cast at anyone."""
    u = (ults or {}).get(str(hero_id)) or {}
    aid = u.get("abilityId")
    for a in (dota_live.hero_profile(hero_id).get("abilities") or []):
        if a.get("abilityId") == aid:
            return bool(a.get("passive"))
    return False


_SCEPTER_KEYS = {"ultimate_scepter", "ultimate_scepter_2", "aghanims_scepter"}
_SHARD_KEYS = {"aghanims_shard"}


def _upgrades_at(track: HeroTrack, t: float) -> dict:
    """Does this hero own Aghanim's Scepter / Shard at time t?"""
    log = getattr(track, "purchase_log", None) or []
    return {
        "scepter": items_mod.has_any(log, t, _SCEPTER_KEYS),
        "shard": items_mod.has_any(log, t, _SHARD_KEYS),
    }


def _kit_state(track: HeroTrack, t: float, ab_meta: dict,
               kit: list[dict], ult_id: int | None = None) -> list[int]:
    """Per-ability state at time t, positional to `kit`.

    -2 = not unlocked (scepter/shard ability they do not own yet),
    -1 = ult not skilled yet, 0 = ready, >0 = seconds of cooldown left.
    Cooldown is inferred from the last observed cast, so an ability we never
    saw cast reads as ready rather than as a guess. Only the ultimate is
    reported as unskilled, because level 6 is the only skill point we can
    infer — basics we cannot know, so we never claim they are unavailable.
    """
    lvl = level_at(track, t) or 1
    up = _upgrades_at(track, t)
    out = []
    for ab in kit:
        aid = ab["id"]
        if ab.get("req") and not up.get(ab["req"]):
            out.append(-2)
            continue
        if ab.get("ult") and not abilities_mod.ult_rank_available(lvl):
            out.append(-1)
            continue
        if ult_id is not None and aid == ult_id and not abilities_mod.ult_rank_available(lvl):
            out.append(-1)
            continue
        last = None
        for ct, ca, _ in track.ability_casts:
            if ca == aid and ct <= t:
                last = ct
        cd = abilities_mod.cooldown(aid, lvl, ab_meta)
        if last is None or cd is None:
            out.append(0)
        else:
            out.append(int(max(0.0, cd - (t - last))))
    return out


def _live_stats(match: MatchData, track: HeroTrack, t: float) -> dict:
    """Scoreboard line AS AT time t — never the end-of-match totals.

    Showing final stats on a 1:19 frame is the same class of bug as showing
    the finished build there: it reads as fact and is wrong by 20 minutes.
    """
    cs, gold = _econ_at(track, t)
    kills = assists = 0
    for other in match.players:
        if other.is_radiant == track.is_radiant:
            continue
        for d in other.deaths:
            if d.time > t:
                continue
            if d.killer == track.hero_id:
                kills += 1
            elif track.hero_id in (d.assists or []):
                assists += 1
    deaths = sum(1 for d in track.deaths if d.time <= t)
    return {"k": kills, "d": deaths, "a": assists, "cs": cs, "gold": gold}


def _cast_feed(tracks: list[HeroTrack], t: float, ab_meta: dict,
               item_db: dict, ults: dict, window: float = CAST_LOOKBACK) -> list[dict]:
    """Ability + item uses in the seconds before t, for the given heroes."""
    t0 = t - window
    skip_items = {
        "tango", "clarity", "flask", "enchanted_mango", "faerie_fire",
        "ward_observer", "ward_sentry", "tpscroll", "magic_stick", "magic_wand",
        "phase_boots", "power_treads", "arcane_boots", "tranquil_boots",
        "quelling_blade", "branches", "blood_grenade", "smoke_of_deceit", "dust",
    }
    ult_ids = {u["abilityId"] for u in ults.values()}
    rows = []
    for tr in tracks:
        for ct, aid, _target in tr.ability_casts:
            if ct < t0 or ct > t:
                continue
            if abilities_mod.is_noise(aid, ab_meta):
                continue
            info = abilities_mod.info(aid, ab_meta)
            rows.append({
                "t": ct, "rel": round(ct - t, 1),
                "kind": "ability", "heroId": tr.hero_id,
                "name": info["name"], "icon": info.get("icon"),
                "ult": aid in ult_ids, "abilityId": aid,
            })
        for ct, iid, _target in tr.item_casts:
            if ct < t0 or ct > t:
                continue
            meta = item_db.get(str(iid)) or {}
            key = meta.get("key") or ""
            if key in skip_items:
                continue
            rows.append({
                "t": ct, "rel": round(ct - t, 1),
                "kind": "item", "heroId": tr.hero_id,
                "name": meta.get("name") or f"Item {iid}",
                "icon": meta.get("icon"),
                "ult": False, "itemId": iid,
            })
    rows.sort(key=lambda r: r["t"])
    return rows


def _situation(f: dict, allies: list, enemies: list, killed_by: dict | None) -> str:
    """One plain-English snapshot of the fight geometry."""
    parts = []
    n_e, n_a = len(enemies), len(allies)
    if n_e == 0 and n_a == 0:
        parts.append("No other living heroes were near you on the map.")
    else:
        parts.append(f"{n_a} all{'y' if n_a == 1 else 'ies'} and "
                     f"{n_e} enem{'y' if n_e == 1 else 'ies'} alive nearby.")
    if enemies:
        closest = min(enemies, key=lambda m: m["dist"])
        parts.append(f"Closest threat: {closest['name']} (~{closest['dist']} units).")
    if killed_by:
        if killed_by.get("assists"):
            names = ", ".join(h["name"] for h in killed_by.get("assistHeroes", [])[:3])
            extra = f" (helped by {names})" if names else f" (+{killed_by['assists']})"
            parts.append(f"Finished by {killed_by['name']}{extra}.")
        else:
            parts.append(f"Finished by {killed_by['name']}.")
    if f.get("in_enemy_half"):
        parts.append("You were on their side of the map.")
    if f.get("near_enemy_tower"):
        parts.append("Enemy tower range was in play.")
    if not f.get("warded"):
        parts.append("No allied Observer covered the spot.")
    return " ".join(parts)


def _marker_for(track: HeroTrack, t: float, me: HeroTrack, killers: set,
                death_xy: tuple[float, float] | None, hmeta,
                include_dead: bool = False) -> dict | None:
    """Build a map marker. `include_dead` keeps the death victim visible at t."""
    is_dead = not alive(track, t)
    if is_dead and not (include_dead and track is me):
        return None
    if track is me and death_xy is not None and include_dead:
        px, py = P(death_xy[0], death_xy[1])
        p_x, p_y = death_xy
    else:
        p = pos_at(track, t)
        if not p:
            return None
        px, py = P(p.x, p.y)
        p_x, p_y = p.x, p.y
    name, ic = hmeta(track.hero_id)
    row = hp_at(track, t)
    hp, mhp, mp, mmp = row if row else (None, None, None, None)
    dist_u = 0
    if death_xy is not None:
        dist_u = round(math.hypot(p_x - death_xy[0], p_y - death_xy[1]) * WORLD_PER_GRID)
    return {
        "heroId": track.hero_id,
        "name": name,
        "icon": ic,
        "x": px,
        "y": py,
        "isRadiant": track.is_radiant,
        "me": track is me,
        "killer": track.hero_id in killers,
        "dist": dist_u,
        "hp": hp, "maxHp": mhp, "mp": mp, "maxMp": mmp,
        "level": level_at(track, t),
        "hasBlink": items_mod.has_any(track.purchase_log, t, items_mod.BLINK_KEYS),
        "hasForce": items_mod.has_any(track.purchase_log, t, items_mod.FORCE_KEYS),
        "hasTravel": items_mod.has_any(track.purchase_log, t, items_mod.TP_ITEM_KEYS),
    }


def _loadout_row(h, t, item_db, hmeta, killers) -> dict:
    name, ic = hmeta(h.hero_id)
    e_items = _inventory_for(h, t, item_db)
    has_blink = items_mod.has_any(h.purchase_log, t, items_mod.BLINK_KEYS)
    if not has_blink:
        has_blink = any(it.get("key") in items_mod.BLINK_KEYS for it in e_items)
    has_force = items_mod.has_any(h.purchase_log, t, items_mod.FORCE_KEYS)
    if not has_force:
        has_force = any(it.get("key") in items_mod.FORCE_KEYS for it in e_items)
    return {
        "heroId": h.hero_id, "name": name, "icon": ic,
        "items": e_items, "hasBlink": has_blink, "hasForce": has_force,
        "involved": h.hero_id in killers,
        "alive": alive(h, t),
    }


def _inventory_for(track: HeroTrack, t: float, item_db: dict) -> list[dict]:
    """Inventory at time t, from time-accurate sources ONLY.

    Deliberately never falls back to `final_items`: those are the END-OF-MATCH
    items, so using them here showed a finished build on a 1-minute death. An
    empty list is the correct answer for an early death — say so, don't invent.
    """
    if track.purchase_log:
        # Authoritative and timestamped. Minor items included so a minute-one
        # inventory shows the tangos and branches they actually held.
        return items_mod.snapshot_items(track.purchase_log, t, include_minor=True)
    main_ids, _neutral = items_at(track, t)
    if main_ids:
        return items_mod.from_item_ids(main_ids, item_db)
    return []


# Abilities that move the VICTIM. A hero yanked by one of these shows a big
# position jump that is NOT their own mobility — calling it "Blink" is wrong.
_PULLS_VICTIM = {
    "pudge_meat_hook": "Hooked",
    "magnataur_skewer": "Skewered",
    "batrider_flaming_lasso": "Lassoed",
    "tiny_toss": "Tossed",
    "vengefulspirit_nether_swap": "Swapped",
    "disruptor_glimpse": "Glimpsed",
    "clockwerk_hookshot": "Hookshot",
    "earth_spirit_boulder_smash": "Smashed",
    "spirit_breaker_charge_of_darkness": "Charged",
}
# Abilities that move the CASTER themselves.
_SELF_MOVE = {
    "antimage_blink": "Blink", "queenofpain_blink": "Blink",
    "faceless_void_time_walk": "Time Walk", "riki_blink_strike": "Blink Strike",
    "storm_spirit_ball_lightning": "Ball Lightning", "slark_pounce": "Pounce",
    "mirana_leap": "Leap", "sandking_burrowstrike": "Burrowstrike",
    "ember_spirit_fire_remnant": "Remnant", "puck_illusory_orb": "Orb",
    "morphling_waveform": "Waveform", "spectre_haunt": "Haunt",
    "nyx_assassin_vendetta": "Vendetta", "life_stealer_infest": "Infest",
}
_JUMP_WINDOW = 2.0          # seconds a cast can explain a jump
_JUMP_PX = 22               # px between frames that counts as a teleport-sized jump
# Towers grant vision. Dota daytime tower vision is ~1900 units.
TOWER_VISION_UNITS = 1900


def _explain_jump(match: MatchData, hero: HeroTrack, t: float,
                  ab_meta: dict, item_db: dict) -> str | None:
    """Name the cause of a position jump, or None if we genuinely don't know.

    Guessing "Blink" for any mid-range jump was producing false blinks for
    heroes who never owned one — most visibly a Pudge hook on the victim.
    """
    lo = t - _JUMP_WINDOW
    # 1. Did an enemy yank them?
    for other in match.players:
        if other.is_radiant == hero.is_radiant:
            continue
        for ct, aid, _tgt in other.ability_casts:
            if lo <= ct <= t + 0.3:
                key = (abilities_mod.info(aid, ab_meta) or {}).get("key") or ""
                if key in _PULLS_VICTIM:
                    return _PULLS_VICTIM[key]
    # 2. Did they move themselves with a spell?
    for ct, aid, _tgt in hero.ability_casts:
        if lo <= ct <= t + 0.3:
            key = (abilities_mod.info(aid, ab_meta) or {}).get("key") or ""
            if key in _SELF_MOVE:
                return _SELF_MOVE[key]
    # 3. Did they press a mobility item?
    for ct, iid, _tgt in hero.item_casts:
        if lo <= ct <= t + 0.3:
            key = (item_db.get(str(iid)) or {}).get("key") or ""
            if key in items_mod.BLINK_KEYS:
                return "Blink"
            if key in items_mod.FORCE_KEYS:
                return "Force"
            if key in items_mod.TP_ITEM_KEYS:
                return "TP"
    return None


def _cast_events(match: MatchData, me: HeroTrack, t0: float, t1: float,
                 ab_meta: dict, ults: dict, item_db: dict, hmeta) -> list[dict]:
    """Every notable ability + item cast in [t0, t1], positioned for playback.

    Not just ultimates — a Meat Hook or a Blink is exactly what you need to see
    to understand a death, so all non-noise casts are included.
    """
    ult_ids = {u["abilityId"] for u in ults.values()}
    events = []
    for tr in match.players:
        name, hic = hmeta(tr.hero_id)
        for ct, aid, _tgt in tr.ability_casts:
            if ct < t0 - 0.05 or ct > t1 + 0.05:
                continue
            if abilities_mod.is_noise(aid, ab_meta):
                continue
            info = abilities_mod.info(aid, ab_meta)
            pos = pos_at(tr, ct)
            if not pos or not info.get("icon"):
                continue
            x, y = P(pos.x, pos.y)
            events.append({
                "t": round(ct, 2), "rel": round(ct - t1, 1),
                "heroId": tr.hero_id, "hero": name, "heroIcon": hic,
                "name": info.get("name") or "Ability", "icon": info.get("icon"),
                "x": x, "y": y, "isRadiant": tr.is_radiant,
                "me": tr is me, "ult": aid in ult_ids, "kind": "ability",
            })
        for ct, iid, _tgt in tr.item_casts:
            if ct < t0 - 0.05 or ct > t1 + 0.05:
                continue
            meta = item_db.get(str(iid)) or {}
            key = meta.get("key") or ""
            if not key or key in status_mod._ITEM_MAP_SKIP or not meta.get("icon"):
                continue
            pos = pos_at(tr, ct)
            if not pos:
                continue
            x, y = P(pos.x, pos.y)
            events.append({
                "t": round(ct, 2), "rel": round(ct - t1, 1),
                "heroId": tr.hero_id, "hero": name, "heroIcon": hic,
                "name": meta.get("name") or key, "icon": meta.get("icon"),
                "x": x, "y": y, "isRadiant": tr.is_radiant,
                "me": tr is me, "ult": False, "kind": "item",
            })
    events.sort(key=lambda e: e["t"])
    return events


def _build_playback(match: MatchData, me: HeroTrack, death_t: float,
                    death_xy: tuple[float, float], killers: set, hmeta,
                    ab_meta: dict | None = None, ults: dict | None = None,
                    item_db: dict | None = None, kits: dict | None = None) -> dict:
    """Frame strip from (death - 10s) → death so the user can scrub the lead-up."""
    t0 = max(death_t - PLAYBACK_BEFORE, -90.0)
    frames = []
    t = t0
    # Include the exact death timestamp as the final frame.
    times = []
    while t < death_t - 1e-6:
        times.append(round(t, 2))
        t += PLAYBACK_STEP
    times.append(round(death_t, 2))

    status_iv = {}
    if ab_meta is not None and item_db is not None:
        status_iv = status_mod.build_status_intervals(
            match, ab_meta, item_db, t0, death_t)

    prev_xy: dict[int, tuple[float, float]] = {}
    for ft in times:
        markers = []
        at_death = abs(ft - death_t) < 1e-6
        for h in match.players:
            m = _marker_for(h, ft, me, killers, death_xy, hmeta,
                            include_dead=at_death)
            if m:
                if at_death:
                    row = hp_at(h, ft - STATE_LOOKBACK)
                    if row:
                        m["hp"], m["maxHp"], m["mp"], m["maxMp"] = row
                m["statuses"] = status_mod.statuses_at(status_iv, h.hero_id, ft)
                if kits and ab_meta is not None:
                    u = (ults or {}).get(str(h.hero_id)) or {}
                    m["ab"] = _kit_state(h, ft, ab_meta,
                                         kits.get(str(h.hero_id)) or [],
                                         u.get("abilityId"))
                m["live"] = _live_stats(match, h, ft)
                m["up"] = _upgrades_at(h, ft)
                # Explain any teleport-sized jump from the previous frame using
                # actual cast data, rather than guessing from distance alone.
                prev = prev_xy.get(h.hero_id)
                if prev and ab_meta is not None and item_db is not None:
                    if math.hypot(m["x"] - prev[0], m["y"] - prev[1]) >= _JUMP_PX:
                        m["jump"] = _explain_jump(match, h, ft, ab_meta, item_db)
                prev_xy[h.hero_id] = (m["x"], m["y"])
                markers.append(m)
        wards = [{
            "x": P(w.x, w.y)[0], "y": P(w.x, w.y)[1],
            "isRadiant": w.is_radiant,
            "kind": getattr(w, "kind", "observer") or "observer",
        } for w in match.wards if w.time_from <= ft <= w.time_to]
        frames.append({
            "t": ft,
            "rel": round(ft - death_t, 2),
            "markers": markers,
            "wards": wards,
            "deadBuildings": sorted(match.dead_buildings(ft)),
        })

    # Path crumbs for drawing the trail up to the scrubber head.
    me_path = []
    for p in me.positions:
        if t0 - 0.5 <= p.time <= death_t + 0.05:
            x, y = P(p.x, p.y)
            me_path.append({"t": p.time, "x": x, "y": y})
    casts = []
    if ab_meta is not None and ults is not None and item_db is not None:
        casts = _cast_events(match, me, t0, death_t, ab_meta, ults, item_db, hmeta)
    return {
        "t0": round(t0, 2),
        "t1": round(death_t, 2),
        "step": PLAYBACK_STEP,
        "frames": frames,
        "mePath": me_path,
        "casts": casts,
    }


def build_report(match: MatchData, me: HeroTrack, analyses: list[DeathAnalysis],
                 coach_advice: dict | None = None) -> dict:
    hero = heroes.load()
    item = items_mod.load()
    ab_meta = abilities_mod.load_meta()
    hero_abs = abilities_mod.load_hero_abilities()
    # Kits are static per hero — ship them once and let frames carry only state.
    kits = {str(h.hero_id): _kit_of(h.hero_id, ab_meta, hero_abs)
            for h in match.players}
    ults = abilities_mod.load_ultimates()

    def hmeta(hid):
        h = hero.get(str(hid), {})
        return h.get("name", f"hero {hid}"), h.get("icon")

    # In-game style scoreboard order: Radiant left, Dire right, each sorted 1-5.
    scoreboard = {"radiant": [], "dire": []}
    for side in (True, False):
        team = [h for h in match.players if h.is_radiant == side]
        pos = _positions(team)
        for h in sorted(team, key=lambda p: pos.get(p.hero_id, 9)):
            name, ic = hmeta(h.hero_id)
            (scoreboard["radiant"] if side else scoreboard["dire"]).append({
                "heroId": h.hero_id, "name": name, "icon": ic,
                "isRadiant": h.is_radiant, "me": h is me,
                "pos": pos.get(h.hero_id),
                "stats": getattr(h, "stats", {}) or {},
            })

    # Per-death movement diagnosis (awareness vs execution), keyed by death index.
    diags = {}
    for a in analyses:
        dg = diagnose_mod.movement(match, me, a)
        if dg:
            diags[a.index] = dg

    deaths = []
    for a in sorted(analyses, key=lambda x: x.time):
        t, d = a.time, a.death
        killers = set([d.killer] if d and d.killer else []) | set(d.assists if d else [])
        copy = _copy_for(a.label)
        sev = _severity(a.score, a.label)
        death_xy = (a.me.x, a.me.y)

        markers = []
        for h in match.players:
            m = _marker_for(h, t, me, killers, death_xy, hmeta, include_dead=True)
            if not m:
                continue
            row = hp_at(h, t - STATE_LOOKBACK)
            if row:
                m["hp"], m["maxHp"], m["mp"], m["maxMp"] = row
            markers.append(m)
        # Keep "you" first for layout anchors.
        markers.sort(key=lambda m: (0 if m["me"] else 1, m["dist"]))

        trails = {"me": _trail_segments(me, t - 15, t), "killers": []}
        mx, my = P(a.me.x, a.me.y)
        for h in match.enemies_of(me):
            involved = h.hero_id in killers or any(
                (not m["isRadiant"] if me.is_radiant else m["isRadiant"])
                and m["heroId"] == h.hero_id
                and math.hypot(m["x"] - mx, m["y"] - my) < 90 for m in markers)
            if involved and alive(h, t):
                trails["killers"].extend(_trail_segments(h, t - 10, t))

        wards = [{
            "x": P(w.x, w.y)[0], "y": P(w.x, w.y)[1],
            "isRadiant": w.is_radiant,
            "kind": getattr(w, "kind", "observer") or "observer",
        } for w in match.wards if w.time_from <= t <= w.time_to]

        # Prefer OpenDota purchase_log — STRATZ inventoryEvents are often empty.
        inv = _inventory_for(me, t, item)
        # Neutral must come from a timestamped source. `final_neutral` is the
        # end-of-match one, and showing it at 1:19 (before neutrals even drop)
        # is a lie — so if we can't time it, we don't show it.
        neutral = None
        _ids, neutral_id = items_at(me, t)
        if neutral_id:
            n = item.get(str(neutral_id), {})
            if n:
                neutral = {"name": n.get("name", f"item {neutral_id}"),
                           "icon": n.get("icon")}

        killed_by = None
        if d and d.killer:
            assist_rows = []
            for aid in (d.assists or []):
                an, ai = hmeta(aid)
                assist_rows.append({"name": an, "icon": ai})
            killed_by = {"name": hmeta(d.killer)[0], "icon": hmeta(d.killer)[1],
                         "assists": len(d.assists), "assistHeroes": assist_rows}

        allies_here = [m for m in markers if m["isRadiant"] == me.is_radiant and not m["me"]]
        enemies_here = [m for m in markers if m["isRadiant"] != me.is_radiant]
        nearest_enemy = min((m["dist"] for m in enemies_here), default=None)
        farm = _farm_finding(me, t)
        findings = _findings(a.features)
        if farm:
            findings.insert(0, farm)

        # Team inventories at death — OpenDota purchase_log (same source Dotabuff uses).
        enemy_loadouts = []
        ally_loadouts = []
        blink_enemies = []
        for h in match.enemies_of(me):
            row = _loadout_row(h, t, item, hmeta, killers)
            enemy_loadouts.append(row)
            if row["hasBlink"]:
                blink_enemies.append(row["name"])
        for h in match.team_of(me):
            ally_loadouts.append(_loadout_row(h, t, item, hmeta, killers))
        enemy_loadouts.sort(key=lambda r: (0 if r["involved"] else 1,
                                           0 if r["hasBlink"] or r["hasForce"] else 1,
                                           r["name"]))
        ally_loadouts.sort(key=lambda r: (0 if r["hasBlink"] or r["hasForce"] else 1,
                                          r["name"]))
        if blink_enemies:
            findings.insert(0, {
                "tone": "warn",
                "title": "Enemy Blink",
                "text": (f"{', '.join(blink_enemies)} already had Blink Dagger — "
                         "assume roughly 1200 units of instant gap close and "
                         "keep that much more distance than feels necessary."),
            })

        my_ult = _ult_status(me, t, ab_meta, ults)
        if my_ult:
            if my_ult["state"] == "ready":
                findings.insert(0, {
                    "tone": "warn", "title": f"{my_ult['name']} ready",
                    "text": f"{my_ult['name']} was off cooldown when you died.",
                })
            elif my_ult["state"] == "cooldown":
                findings.insert(0, {
                    "tone": "ok", "title": f"{my_ult['name']} on CD",
                    "text": my_ult["text"],
                })

        enemy_ults = []
        for h in match.enemies_of(me):
            st = _ult_status(h, t, ab_meta, ults)
            if not st:
                continue
            name, ic = hmeta(h.hero_id)
            enemy_ults.append({
                "heroId": h.hero_id, "hero": name, "heroIcon": ic,
                "passive": _ult_is_passive(h.hero_id, ults),
                **st,
            })
        ready_enemy_ults = [u for u in enemy_ults
                            if u.get("state") == "ready" and not u.get("passive")]
        if ready_enemy_ults:
            names = ", ".join(u["hero"] + "'s " + u["name"] for u in ready_enemy_ults[:3])
            findings.insert(0, {
                "tone": "bad", "title": "Enemy ults up",
                "text": (f"{names} could be cast on you at that moment — "
                         "that is the damage window you walked into."),
            })

        # Casts from everyone involved in the lead-up window.
        casts = _cast_feed(list(match.players), t, ab_meta, item, ults)
        casts.sort(key=lambda c: (0 if c.get("ult") else 1 if c.get("kind") == "item" else 2, c["t"]))
        casts = casts[:14]
        casts.sort(key=lambda c: c["t"])
        hlookup = {p.hero_id: hmeta(p.hero_id) for p in match.players}
        for c in casts:
            nm, ic = hlookup.get(c["heroId"], (f"hero {c['heroId']}", None))
            c["hero"] = nm
            c["heroIcon"] = ic

        situation = _situation(a.features, allies_here, enemies_here, killed_by)
        story = _story(a.features, farm)

        roster = [m for m in markers if m["me"]] + sorted(
            [m for m in markers if not m["me"]], key=lambda m: m["dist"])

        mm, ss = int(t // 60), int(t % 60)
        gold_lost = (d.gold_lost if d else 0) or 0
        time_dead = (d.time_dead if d else 0) or 0
        deaths.append({
            "idx": a.index, "time": t, "clock": f"{mm}:{ss:02d}",
            "diagnosis": diags.get(a.index),
            "label": a.label, "title": copy["title"], "blurb": copy["blurb"],
            "tip": copy["tip"], "story": story, "score": a.score, "severity": sev,
            "chips": _chips(d), "findings": findings,
            "situation": situation,
            "counts": {
                "allies": len(allies_here), "enemies": len(enemies_here),
                "nearestEnemy": nearest_enemy,
            },
            "killedBy": killed_by, "markers": markers, "roster": roster,
            "trails": trails, "wards": wards,
            "deadBuildings": sorted(match.dead_buildings(t)),
            "items": inv, "neutral": neutral,
            "enemyLoadouts": enemy_loadouts,
            "allyLoadouts": ally_loadouts,
            "myUlt": my_ult,
            "enemyUlts": enemy_ults,
            "casts": casts,
            "cost": {"gold": round(gold_lost), "respawn": round(time_dead)},
            "playback": _build_playback(match, me, t, death_xy, killers, hmeta,
                                        ab_meta, ults, item, kits),
        })

    vision_px = 1600 / WORLD_PER_GRID / (C.GAME_MAX - C.GAME_MIN) * M
    my_name, my_icon = hmeta(me.hero_id)
    won = (match.radiant_win == me.is_radiant) if match.radiant_win is not None else None

    focus = max(deaths, key=lambda d: d["score"]) if deaths else None
    total_gold = sum(d["cost"]["gold"] for d in deaths)
    critical_n = sum(1 for d in deaths if d["severity"]["key"] == "critical")
    notable_n = sum(1 for d in deaths if d["severity"]["key"] == "notable")
    focus_list_i = next((i for i, d in enumerate(deaths) if d is focus), 0) if focus else 0

    habit = diagnose_mod.habits(match, me, analyses, diags, item, hmeta)

    # Per-death AI advice + headline, keyed by death number.
    # The AI rewrites the *displayed* title only; `label` stays the machine
    # category that drives ranking, severity and the habit rollup.
    if coach_advice:
        by_n = {int(d.get("n", -1)): d for d in (coach_advice.get("deaths") or [])}
        for d in deaths:
            entry = by_n.get(d["idx"] + 1) or {}
            d["aiAdvice"] = entry.get("advice")
            if entry.get("title"):
                d["title"] = entry["title"]

    return {
        "matchId": match.match_id,
        "habit": habit,
        "kits": kits,
        "damage": _damage_profile(me, match, ab_meta, item, hmeta),
        "coach": coach_advice,
        "hero": {"name": my_name, "icon": my_icon, "heroId": me.hero_id,
                 "side": "Radiant" if me.is_radiant else "Dire",
                 "isRadiant": me.is_radiant, "won": won},
        "scoreboard": scoreboard,
        "visionR": round(vision_px, 1),
        "towerVisionR": round(
            TOWER_VISION_UNITS / WORLD_PER_GRID / (C.GAME_MAX - C.GAME_MIN) * M, 1),
        "buildings": [{"key": b.key, "x": P(b.x, b.y)[0], "y": P(b.x, b.y)[1],
                       "icon": b.icon, "kind": b.kind,
                       "isRadiant": b.is_radiant} for b in match.buildings],
        "summary": {
            "deathCount": len(deaths),
            "totalGoldLost": total_gold,
            "criticalCount": critical_n,
            "notableCount": notable_n,
            "focusIdx": focus_list_i,
            "focusClock": focus["clock"] if focus else None,
            "focusTitle": focus["title"] if focus else None,
        },
        "deaths": deaths,
    }


def render(match: MatchData, me: HeroTrack, analyses: list[DeathAnalysis],
           out_path: str, coach_advice: dict | None = None) -> str:
    report = build_report(match, me, analyses, coach_advice)
    sprites = {b.icon: _b64(b.icon) for b in match.buildings}
    for ward_icon in ("ward_observer_ally.png", "ward_observer_enemy.png",
                      "ward_sentry_ally.png", "ward_sentry_enemy.png"):
        try:
            sprites[ward_icon] = _b64(ward_icon)
        except Exception:
            pass
    default_idx = report["summary"].get("focusIdx", 0) if report["deaths"] else 0

    doc = _TEMPLATE
    doc = doc.replace("__MAP2_B64__", _b64("minimap_ingame.jpg"))
    doc = doc.replace("__MAP_B64__", _b64("dota_map.png"))
    doc = doc.replace("__REPORT__", json.dumps(report, ensure_ascii=False))
    doc = doc.replace("__SPRITES__", json.dumps(sprites))
    doc = doc.replace("__DEFAULT__", str(default_idx))
    doc = doc.replace("__TITLE__", html.escape(
        f"{report['hero']['name']} — match {report['matchId']}"))
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(doc)
    return out_path


_TEMPLATE = r"""<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700&family=Source+Sans+3:wght@400;500;600;700&display=swap" rel="stylesheet">
<style>
:root{
  --bg:#070b14; --bg-elev:#0e1524; --bg-soft:#151d2e; --bg-lift:#1b2437;
  --line:#25314a; --line-soft:#1a2336;
  --text:#eef2f9; --muted:#9aa8bf; --faint:#697990;
  --you:#fbbf24; --radiant:#4ade80; --dire:#f87171;
  --ward:#e0b83a; --eward:#b56fd4;
  --accent:#a78bfa; --accent-dim:rgba(167,139,250,.14);
  --ok:#4ade80; --warn:#fbbf24; --bad:#f87171; --fight:#60a5fa;
  --r-sm:8px; --r:12px; --r-lg:18px;
  --font-display:"Outfit",system-ui,sans-serif;
  --font-body:"Source Sans 3",system-ui,sans-serif;
  --shadow:0 1px 2px rgba(0,0,0,.4), 0 8px 28px rgba(0,0,0,.28);
}
*{box-sizing:border-box}
html{scroll-behavior:smooth}
html,body{margin:0}
body{
  font-family:var(--font-body);font-size:15px;line-height:1.6;
  background:
    radial-gradient(1200px 700px at 12% -10%, #16223c 0%, transparent 55%),
    radial-gradient(1000px 600px at 100% 2%, #1d1726 0%, transparent 50%),
    var(--bg);
  background-attachment:fixed;
  color:var(--text);-webkit-font-smoothing:antialiased;
}
button{font:inherit;color:inherit;background:none;border:0;cursor:pointer;padding:0}
h1,h2,h3{font-family:var(--font-display);margin:0;line-height:1.25}
img{max-width:100%}
.wrap{max-width:1560px;margin:0 auto;padding:0 24px 72px}

/* ---------- section furniture ---------- */
.sec{margin:36px 0 0}
.sec-head{display:flex;align-items:baseline;gap:12px;margin:0 0 14px}
.sec-head h2{font-size:19px;font-weight:650;letter-spacing:-.01em}
.sec-head .hint{font-size:13px;color:var(--faint)}
.eyebrow{font-size:10.5px;font-weight:700;letter-spacing:.16em;text-transform:uppercase;
  color:var(--accent);margin:0 0 8px}
.card{background:var(--bg-elev);border:1px solid var(--line-soft);border-radius:var(--r-lg);
  box-shadow:var(--shadow)}

/* ---------- header ---------- */
.hero-head{
  border-bottom:1px solid var(--line-soft);
  background:linear-gradient(180deg, rgba(14,21,36,.92), rgba(7,11,20,.75));
  backdrop-filter:blur(12px);position:sticky;top:0;z-index:30;
}
.hh-in{max-width:1560px;margin:0 auto;padding:12px 24px;
  display:flex;align-items:center;gap:18px;flex-wrap:wrap}
.hh-id{display:flex;align-items:center;gap:13px;min-width:0}
.hh-por{width:54px;height:54px;border-radius:13px;object-fit:cover;flex:0 0 auto;
  border:2px solid var(--line);box-shadow:0 4px 14px rgba(0,0,0,.5)}
.hh-name{font-family:var(--font-display);font-size:20px;font-weight:650;letter-spacing:-.01em;
  display:flex;align-items:center;gap:9px;flex-wrap:wrap}
.hh-sub{font-size:12.5px;color:var(--faint)}
.badge{font-size:10.5px;font-weight:700;letter-spacing:.09em;text-transform:uppercase;
  padding:3px 9px;border-radius:999px;border:1px solid transparent}
.badge.win{color:#86efac;background:rgba(74,222,128,.13);border-color:rgba(74,222,128,.32)}
.badge.loss{color:#fca5a5;background:rgba(248,113,113,.13);border-color:rgba(248,113,113,.32)}
.hh-stats{display:flex;gap:22px;margin-left:auto;flex-wrap:wrap}
.hh-stat{text-align:right}
.hh-stat b{display:block;font-family:var(--font-display);font-size:18px;font-weight:650;
  letter-spacing:-.01em;line-height:1.2}
.hh-stat span{font-size:10.5px;color:var(--faint);text-transform:uppercase;letter-spacing:.1em}
.hh-draft{display:flex;gap:14px;align-items:center;width:100%;padding-top:2px}
.team{display:flex;gap:5px;align-items:center}
.team.radiant{justify-content:flex-end}
.vs{font-size:11px;color:var(--faint);font-weight:700;letter-spacing:.1em}
.slot{position:relative;width:38px;height:38px;border-radius:9px;overflow:hidden;
  background:#0d1219;border:2px solid transparent;opacity:.86;transition:.15s}
.slot img{width:100%;height:100%;object-fit:cover;display:block}
.slot.radiant{border-color:rgba(74,222,128,.4)}
.slot.dire{border-color:rgba(248,113,113,.4)}
.slot.me{border-color:var(--you);opacity:1;box-shadow:0 0 0 2px rgba(251,191,36,.22)}

/* ---------- verdict ---------- */
.verdict{position:relative;overflow:hidden;padding:26px 30px;margin-top:28px;
  border-radius:var(--r-lg);border:1px solid rgba(167,139,250,.26);
  background:
    radial-gradient(760px 260px at 0% 0%, rgba(167,139,250,.14), transparent 62%),
    var(--bg-elev);
  box-shadow:var(--shadow)}
.verdict::before{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;
  background:linear-gradient(180deg,var(--accent),rgba(167,139,250,.15))}
.verdict h1{font-size:26px;font-weight:650;letter-spacing:-.02em;max-width:44ch;margin:0 0 10px}
.verdict .fix{font-size:15.5px;color:var(--muted);max-width:78ch;margin:0}
.verdict .drill{display:inline-flex;align-items:flex-start;gap:9px;margin:18px 0 0;
  padding:11px 15px;border-radius:var(--r);background:rgba(167,139,250,.1);
  border:1px solid rgba(167,139,250,.22);font-size:14px;color:#e9e2ff;max-width:72ch}
.verdict .drill b{color:var(--accent);white-space:nowrap;font-weight:650}
.overall{font-size:15.5px;color:var(--muted);max-width:82ch;margin:16px 0 0}

/* ---------- strengths / mistakes ---------- */
.cols{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:18px}
.colcard{padding:20px 22px}
.colcard > h3{font-size:13px;font-weight:700;letter-spacing:.1em;text-transform:uppercase;
  display:flex;align-items:center;gap:8px;margin:0 0 14px}
.colcard.good > h3{color:var(--ok)}
.colcard.bad > h3{color:var(--bad)}
.colcard > h3 .ic{width:20px;height:20px;border-radius:6px;display:grid;place-items:center;
  font-size:12px;font-weight:700}
.colcard.good .ic{background:rgba(74,222,128,.15)}
.colcard.bad .ic{background:rgba(248,113,113,.15)}
.pt{padding:13px 0;border-top:1px solid var(--line-soft)}
.pt:first-of-type{border-top:0;padding-top:0}
.pt .ptt{font-size:14.5px;font-weight:650;margin:0 0 4px;font-family:var(--font-display)}
.pt .ptx{font-size:14px;color:var(--muted);margin:0}
.itemcard{padding:20px 22px}
.itemcard p{margin:0;font-size:14.5px;color:var(--muted);max-width:84ch}

/* ---------- habit banner ---------- */
.habit{display:flex;align-items:center;gap:14px;padding:14px 20px;margin-top:18px;
  border-radius:var(--r);border:1px solid var(--line-soft);background:var(--bg-soft);
  font-size:14px;color:var(--muted)}
.habit b{color:var(--text);font-weight:650}
.habit .nem{display:inline-flex;align-items:center;gap:7px}
.habit .nem img{width:26px;height:26px;border-radius:7px;object-fit:cover}

/* ---------- death strip ---------- */
.strip-head{display:flex;align-items:center;gap:14px;margin:0 0 12px;flex-wrap:wrap}
.sort{display:flex;gap:3px;margin-left:auto;background:var(--bg-soft);padding:3px;
  border-radius:999px;border:1px solid var(--line-soft)}
.sort button{font-size:12px;padding:5px 13px;border-radius:999px;color:var(--faint);
  font-weight:600;transition:.15s}
.sort button:hover{color:var(--muted)}
.sort button.on{background:var(--bg-lift);color:var(--text)}
.strip{display:flex;gap:10px;overflow-x:auto;padding:4px 2px 12px;scrollbar-width:thin}
.strip::-webkit-scrollbar{height:7px}
.strip::-webkit-scrollbar-thumb{background:var(--line);border-radius:99px}
.dcard{flex:0 0 auto;width:186px;text-align:left;padding:12px 14px;border-radius:var(--r);
  background:var(--bg-elev);border:1px solid var(--line-soft);transition:.15s;position:relative}
.dcard:hover{border-color:var(--line);transform:translateY(-1px)}
.dcard.on{border-color:var(--accent);background:linear-gradient(180deg,var(--accent-dim),var(--bg-elev));
  box-shadow:0 0 0 1px rgba(167,139,250,.25)}
.dcard .row1{display:flex;align-items:center;gap:7px;margin-bottom:5px}
.dcard .dot{width:7px;height:7px;border-radius:50%;flex:0 0 auto}
.dot.critical{background:var(--bad)} .dot.notable{background:var(--warn)}
.dot.minor{background:var(--faint)} .dot.fight{background:var(--fight)}
.dcard .clock{font-family:var(--font-display);font-size:14px;font-weight:650}
.dcard .num{margin-left:auto;font-size:11px;color:var(--faint)}
.dcard .title{font-size:13px;color:var(--muted);line-height:1.4;
  display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.dcard .flag{display:inline-block;margin-top:7px;font-size:9.5px;font-weight:700;
  letter-spacing:.09em;text-transform:uppercase;color:var(--accent);
  background:var(--accent-dim);padding:2px 7px;border-radius:999px}

/* ---------- stage: teams flank the map ---------- */
.stage{display:grid;grid-template-columns:216px minmax(0,1fr) 216px;gap:18px;align-items:start}
.team-col{display:flex;flex-direction:column;gap:7px;min-width:0}
.team-col h3{font-size:11px;font-weight:700;letter-spacing:.13em;text-transform:uppercase;
  padding:0 2px 2px}
.srow{min-width:0;padding:9px 10px;border-radius:var(--r);background:var(--bg-elev);
  border:1px solid var(--line-soft);transition:.15s}
.srow.dead{opacity:.42}
.srow.me{border-color:rgba(251,191,36,.45);background:linear-gradient(180deg,rgba(251,191,36,.09),var(--bg-elev))}
.hd{display:grid;grid-template-columns:26px minmax(0,1fr) auto;gap:8px;align-items:center}
.hd > div{min-width:0}
.hd .hp{width:26px;height:26px;border-radius:7px;object-fit:cover}
.nm{font-size:12.5px;font-weight:650;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;
  display:flex;align-items:center;gap:5px}
.po{flex:0 0 auto;min-width:14px;padding:1px 4px;border-radius:4px;
  background:rgba(154,168,191,.16);color:var(--faint);font-size:9.5px;font-weight:700;
  text-align:center}
.srow.me .po{background:rgba(251,191,36,.24);color:var(--you)}
.kda{font-size:11px;color:var(--faint);font-variant-numeric:tabular-nums}
.lvl{font-size:10.5px;font-weight:700;color:var(--muted);background:var(--bg-lift);
  padding:2px 6px;border-radius:5px}
.abs{display:flex;gap:3px;margin:7px 0 0}
.ab{position:relative;width:23px;height:23px;border-radius:5px;overflow:hidden;
  background:#0a1120;border:1px solid var(--line-soft);flex:0 0 auto}
.ab img{width:100%;height:100%;object-fit:cover;display:block}
.ab.cool img{filter:grayscale(1) brightness(.42)}
.ab.locked{opacity:.28}
.ab.locked img{filter:grayscale(1) brightness(.5)}
.ab i{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;
  font-style:normal;font-size:10px;font-weight:700;color:#fff;text-shadow:0 1px 3px #000}
.bars{display:flex;flex-direction:column;gap:3px;margin:7px 0 0}
.sbar{height:4px;border-radius:99px;background:rgba(0,0,0,.45);overflow:hidden}
.sbar i{display:block;height:100%;border-radius:99px;transition:width .12s linear}
.num{display:flex;justify-content:space-between;font-size:10.5px;color:var(--faint);
  margin:6px 0 0;font-variant-numeric:tabular-nums}
.its{display:grid;grid-template-columns:repeat(6,1fr);gap:3px;margin:7px 0 0}
.its img,.its .sl{width:100%;aspect-ratio:1.35/1;border-radius:4px;object-fit:cover;display:block}
.its .sl{background:rgba(0,0,0,.32);border:1px solid var(--line-soft)}
.st{display:flex;flex-wrap:wrap;gap:4px;margin:7px 0 0;font-size:10px;font-weight:600}

/* ---------- map ---------- */
.mapwrap{display:flex;flex-direction:column;align-items:center;gap:12px;min-width:0}
.map-stage{position:relative;width:100%;max-width:760px;aspect-ratio:1/1}
#map{width:100%;height:100%;display:block;border-radius:var(--r-lg);
  border:1px solid var(--line);background:#080c14;box-shadow:var(--shadow)}
.map-toggle{position:absolute;top:11px;right:11px;font-size:11px;font-weight:600;
  padding:6px 11px;border-radius:999px;background:rgba(7,11,20,.82);color:var(--muted);
  border:1px solid var(--line);backdrop-filter:blur(6px);transition:.15s}
.map-toggle:hover{color:var(--text);border-color:var(--accent)}
.transport{display:flex;align-items:center;gap:16px;width:100%;max-width:760px;
  padding:12px 16px;border-radius:var(--r);background:var(--bg-elev);
  border:1px solid var(--line-soft)}
.play{width:38px;height:38px;border-radius:50%;flex:0 0 auto;display:grid;place-items:center;
  background:var(--accent);color:#1a1030;font-size:14px;transition:.15s}
.play:hover{filter:brightness(1.12)}
.scrub{flex:1;min-width:0}
.scrub input{width:100%;accent-color:var(--accent);cursor:pointer}
.times{display:flex;justify-content:space-between;font-size:11px;color:var(--faint);
  margin-top:3px;font-variant-numeric:tabular-nums}
.times strong{color:var(--text);font-weight:650}
.nav-death{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--muted)}
.nav-death button{width:28px;height:28px;border-radius:8px;background:var(--bg-lift);
  display:grid;place-items:center;transition:.15s}
.nav-death button:hover{background:var(--line);color:var(--text)}
.kbd{font-size:10px;color:var(--faint);border:1px solid var(--line);
  padding:2px 6px;border-radius:5px}

/* ---------- detail panel ---------- */
/* Full width now, so the detail blocks tile instead of forming one long
   column with a thousand pixels of dead space beside it. */
.panel{margin-top:20px;padding:24px 26px;
  display:grid;grid-template-columns:repeat(auto-fit,minmax(330px,1fr));
  gap:18px;align-items:start}
.panel > .sev-tag,.panel > h1,.panel > .blurb,.panel > .chips,
.panel > details,.panel > .leg{grid-column:1/-1;margin:0}
.panel > .sev-tag{justify-self:start}
.panel > h1,.panel > .blurb{max-width:70ch}
.panel > .diag,.panel > .coach,.panel > .lesson,.panel > .story,
.panel > .section,.panel > .next{margin:0;align-self:start}
.panel > details > .inner,.panel > details > div{margin-top:10px}
.sev-tag{display:inline-block;font-size:10px;font-weight:700;letter-spacing:.11em;
  text-transform:uppercase;padding:4px 10px;border-radius:999px;margin-bottom:11px}
.sev-tag.critical{color:#fca5a5;background:rgba(248,113,113,.14)}
.sev-tag.notable{color:#fcd34d;background:rgba(251,191,36,.14)}
.sev-tag.minor{color:var(--muted);background:rgba(154,168,191,.12)}
.sev-tag.fight{color:#93c5fd;background:rgba(96,165,250,.14)}
.panel h1{font-size:23px;font-weight:650;letter-spacing:-.015em;margin:0 0 8px;max-width:40ch}
.panel .blurb{font-size:14.5px;color:var(--muted);margin:0 0 18px;max-width:80ch}
.pgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:20px}
.chips{display:flex;flex-wrap:wrap;gap:7px}
.chip{font-size:12px;padding:5px 11px;border-radius:999px;background:var(--bg-soft);
  border:1px solid var(--line-soft);color:var(--muted)}
.subh{font-size:11px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;
  color:var(--faint);margin:0 0 10px}
.coach{padding:16px 18px;border-radius:var(--r);margin:0 0 18px;
  background:rgba(167,139,250,.08);border:1px solid rgba(167,139,250,.2)}
.coach .k{display:block;font-size:10px;font-weight:700;letter-spacing:.14em;
  text-transform:uppercase;color:var(--accent);margin-bottom:7px}
.coach .t{font-size:14.5px;font-weight:650;margin:0 0 7px;line-height:1.45}
.coach .x{font-size:14px;color:var(--muted);line-height:1.55;margin:0}
.diag{padding:14px 16px;border-radius:var(--r);margin:0 0 18px;background:var(--bg-soft);
  border-left:3px solid var(--line)}
.diag.awareness{border-left-color:var(--warn)}
.diag.execution{border-left-color:var(--bad)}
.diag.committed{border-left-color:var(--fight)}
.diag .k{display:block;font-size:10px;font-weight:700;letter-spacing:.13em;
  text-transform:uppercase;color:var(--faint);margin-bottom:6px}
.diag .t{font-size:14.5px;font-weight:650;margin:0 0 6px}
.diag .x{font-size:14px;color:var(--muted);margin:0}
.finding{padding:11px 0;border-top:1px solid var(--line-soft)}
.finding:first-of-type{border-top:0;padding-top:0}
.finding .ft{font-size:14px;font-weight:650;margin-bottom:3px}
.finding .fx{font-size:13.5px;color:var(--muted)}
.finding.ok .ft{color:var(--ok)} .finding.bad .ft{color:var(--bad)}
.statuses{display:flex;flex-wrap:wrap;gap:6px}
.stchip{display:inline-flex;align-items:baseline;gap:6px;font-size:11.5px;font-weight:650;
  padding:4px 10px;border-radius:999px;background:var(--bg-soft);border:1px solid var(--line-soft)}
.stchip .src{font-weight:500;text-transform:none;letter-spacing:0;color:var(--faint);font-size:10.5px}
.focus{margin:18px 0 0;padding:0;list-style:none}
.focus li{display:flex;gap:11px;align-items:flex-start;padding:9px 0;font-size:14px;
  border-top:1px solid var(--line-soft)}
.focus li:first-child{border-top:0}
.focus .n{flex:0 0 auto;width:20px;height:20px;border-radius:50%;background:var(--accent-dim);
  color:var(--accent);font-size:11px;font-weight:700;display:grid;place-items:center;margin-top:2px}

/* ---------- upgrade badges ---------- */
.upg{width:23px;height:23px;border-radius:5px;display:grid;place-items:center;flex:0 0 auto;
  font-size:11px;font-weight:800;font-family:var(--font-display)}
.upg.ag{background:rgba(96,165,250,.2);color:#93c5fd;border:1px solid rgba(96,165,250,.45)}
.upg.sh{background:rgba(167,139,250,.2);color:#c4b5fd;border:1px solid rgba(167,139,250,.45)}

/* ---------- panel internals ---------- */
.panel .section{padding:16px 18px;border-radius:var(--r);background:var(--bg-soft);
  border:1px solid var(--line-soft)}
.panel .section > h3{font-size:11px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;
  color:var(--faint);margin:0 0 12px}
.lesson,.story{padding:14px 16px;border-radius:var(--r);background:var(--bg-soft);
  border:1px solid var(--line-soft);font-size:14px;color:var(--muted)}
.lesson .k,.story .k,.next .k{display:block;font-size:10px;font-weight:700;letter-spacing:.13em;
  text-transform:uppercase;color:var(--faint);margin-bottom:7px}
.lesson .t{font-size:14.5px;font-weight:650;color:var(--text);margin:0 0 5px}
.lesson .s{font-size:12.5px;color:var(--faint);margin:0}
.hint{font-size:11.5px;color:var(--faint);font-style:italic;margin-top:8px}

.kb{display:flex;align-items:center;gap:11px;font-size:14px;color:var(--muted)}
.kb img{width:38px;height:38px;border-radius:9px;object-fit:cover;border:2px solid var(--dire)}
.kb b{color:var(--text);font-weight:650}
.meters{display:grid;gap:10px}
.meter{display:grid;grid-template-columns:auto 1fr;gap:2px 10px;align-items:baseline}
.meter .lbl{font-size:11px;color:var(--faint);text-transform:uppercase;letter-spacing:.08em}
.meter .val{font-size:12.5px;color:var(--text);text-align:right;font-variant-numeric:tabular-nums}
.meter .bar{grid-column:1/-1;height:6px;border-radius:99px;background:rgba(0,0,0,.45);overflow:hidden}
.meter .bar i{display:block;height:100%;border-radius:99px}

.findings{display:grid;gap:0}
.panel .lvl{display:inline-block;height:auto !important;font-size:11px}

/* collapsibles */
details.sec{border-radius:var(--r);background:var(--bg-soft);border:1px solid var(--line-soft);
  overflow:hidden}
details.sec > summary{cursor:pointer;padding:13px 16px;font-size:13px;font-weight:650;
  color:var(--muted);list-style:none;display:flex;align-items:center;gap:9px;transition:.15s}
details.sec > summary::-webkit-details-marker{display:none}
details.sec > summary::before{content:"›";font-size:16px;color:var(--faint);
  transition:transform .18s;display:inline-block}
details.sec[open] > summary{color:var(--text);border-bottom:1px solid var(--line-soft)}
details.sec[open] > summary::before{transform:rotate(90deg)}
details.sec > summary:hover{color:var(--text);background:rgba(255,255,255,.02)}
details.sec .inner{padding:16px}

/* roster */
.roster{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:8px}
.rrow{display:grid;grid-template-columns:34px 1fr auto;gap:10px;align-items:center;
  padding:8px 10px;border-radius:var(--r-sm);background:var(--bg-elev);
  border:1px solid var(--line-soft);border-left:3px solid var(--line)}
.rrow.radiant{border-left-color:var(--radiant)}
.rrow.dire{border-left-color:var(--dire)}
.rrow.me{border-left-color:var(--you);background:rgba(251,191,36,.07)}
.rrow img{width:34px;height:34px;border-radius:8px;object-fit:cover}
.rn{font-size:13px;font-weight:650;display:flex;align-items:center;gap:7px}
.rm{font-size:11.5px;color:var(--faint)}
.rd{font-size:11.5px;color:var(--muted);white-space:nowrap;font-variant-numeric:tabular-nums}
.tagkill{font-size:9px;font-weight:700;letter-spacing:.08em;text-transform:uppercase;
  color:#fca5a5;background:rgba(248,113,113,.16);padding:2px 6px;border-radius:99px}

/* item rows */
.items,.eitems{display:flex;flex-wrap:wrap;gap:5px;align-items:center}
.items img,.eitems img{width:41px;aspect-ratio:1.35/1;border-radius:5px;object-fit:cover;
  border:1px solid var(--line-soft)}
.items img{width:48px}
.ph{font-size:10.5px;color:var(--faint);padding:4px 8px;border-radius:5px;
  background:var(--bg-soft);border:1px solid var(--line-soft)}
.neu{border-color:rgba(167,139,250,.5) !important}
.eload{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:9px;
  margin-bottom:14px}
.erow{padding:10px 12px;border-radius:var(--r-sm);background:var(--bg-elev);
  border:1px solid var(--line-soft);border-left:3px solid var(--dire)}
.erow.ally{border-left-color:var(--radiant)}
.etop{display:flex;align-items:center;gap:8px;margin-bottom:8px}
.etop .hero{width:26px;height:26px;border-radius:7px;object-fit:cover}
.ename{font-size:12.5px;font-weight:650}
.eflags{margin-left:auto;font-size:10px;color:var(--warn);font-weight:600}

/* ultimates */
.ults{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:9px}
.ult{display:grid;grid-template-columns:34px 1fr auto;gap:10px;align-items:center;
  padding:9px 11px;border-radius:var(--r-sm);background:var(--bg-elev);
  border:1px solid var(--line-soft)}
.ult img{width:34px;height:34px;border-radius:8px;object-fit:cover}
.un{font-size:12.5px;font-weight:650}
.ux{font-size:11.5px;color:var(--faint)}
.ustate{font-size:9.5px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;
  padding:3px 8px;border-radius:99px;white-space:nowrap}
.ustate.ready{color:#86efac;background:rgba(74,222,128,.16)}
.ustate.cooldown{color:#fcd34d;background:rgba(251,191,36,.16)}
.ustate.unskilled,.ustate.unknown{color:var(--faint);background:rgba(154,168,191,.13)}

/* cast feed */
.casts{display:flex;flex-wrap:wrap;gap:6px}
.tchip{display:inline-flex;align-items:center;gap:6px;font-size:11.5px;padding:4px 10px 4px 4px;
  border-radius:99px;background:var(--bg-elev);border:1px solid var(--line-soft);color:var(--muted)}
.tchip img{width:20px;height:20px;border-radius:50%;object-fit:cover}
.tchip.ult{border-color:rgba(251,191,36,.45);background:rgba(251,191,36,.1);color:#fde68a}
.tchip .tm{font-variant-numeric:tabular-nums;color:var(--faint);font-size:10.5px}
.tchip .who{color:var(--faint);font-size:10.5px}

/* next focus + legend */
.next{padding:16px 18px;border-radius:var(--r);background:rgba(167,139,250,.08);
  border:1px solid rgba(167,139,250,.2)}
.next .k{color:var(--accent)}
.next ol{margin:0;padding:0;list-style:none;display:grid;gap:9px}
.next li{display:flex;gap:10px;align-items:flex-start;font-size:13.5px;color:var(--muted)}
.next .n{flex:0 0 auto;width:19px;height:19px;border-radius:50%;background:var(--accent-dim);
  color:var(--accent);font-size:10.5px;font-weight:700;display:grid;place-items:center;margin-top:2px}
.leg{font-size:11.5px;color:var(--faint);line-height:1.7;padding-top:14px;
  border-top:1px solid var(--line-soft)}
.sw{display:inline-block;width:9px;height:9px;border-radius:50%;margin:0 5px 0 14px;
  vertical-align:middle}

/* ---------- damage profile ---------- */
.dsplit{display:flex;height:10px;border-radius:99px;overflow:hidden;background:var(--bg-soft);
  margin:4px 0 9px}
.dsplit i{display:block;height:100%}
.dlegend{display:flex;gap:16px;flex-wrap:wrap;margin-bottom:16px}
.dlg{display:inline-flex;align-items:center;gap:6px;font-size:12px;color:var(--muted);
  text-transform:capitalize}
.dlg b{width:9px;height:9px;border-radius:2px;display:inline-block}
.dcols{display:grid;grid-template-columns:minmax(0,1.6fr) minmax(0,1fr);gap:22px;align-items:start}
.drow2{display:grid;grid-template-columns:30px minmax(0,1fr) auto;gap:10px;align-items:center;
  padding:5px 0}
.drow2 img,.drow2 .dic{width:30px;height:30px;border-radius:7px;object-fit:cover;
  background:var(--bg-soft);display:grid;place-items:center;font-size:13px;color:var(--faint)}
.dn{font-size:12.5px;font-weight:600;margin-bottom:4px}
.dbar{height:5px;border-radius:99px;background:rgba(0,0,0,.35);overflow:hidden}
.dbar i{display:block;height:100%;border-radius:99px}
.dv{font-size:12px;color:var(--muted);font-variant-numeric:tabular-nums}
.dheroes{display:grid;gap:7px}
.dhero{display:flex;align-items:center;gap:9px;padding:7px 9px;border-radius:var(--r-sm);
  background:var(--bg-soft);border:1px solid var(--line-soft)}
.dhero img{width:28px;height:28px;border-radius:7px;object-fit:cover}
.dhero b{display:block;font-size:12.5px;font-weight:650}
.dhero span{font-size:11px;color:var(--faint)}
@media (max-width:760px){ .dcols{grid-template-columns:1fr} }

/* ---------- responsive ---------- */
@media (max-width:1360px){
  .stage{grid-template-columns:186px minmax(0,1fr) 186px;gap:12px}
}
@media (max-width:1120px){
  .stage{grid-template-columns:1fr}
  .team-col{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:8px}
  .team-col h3{grid-column:1/-1}
  #team-radiant{order:2} .mapwrap{order:1} #team-dire{order:3}
  .map-stage{max-width:640px;margin:0 auto}
}
@media (max-width:760px){
  .wrap{padding:0 14px 56px}
  .hh-in{padding:12px 14px}
  .hh-stats{margin-left:0;width:100%;justify-content:space-between;gap:12px}
  .hh-stat{text-align:left}
  .verdict{padding:20px 18px}
  .verdict h1{font-size:21px}
  .team-col{grid-template-columns:repeat(2,minmax(0,1fr))}
  .transport{flex-wrap:wrap;gap:12px}
  .panel{padding:18px 16px}
}
</style>
</head><body>
<div class="app">
  <header class="hero-head">
    <div class="hh-in">
      <div class="hh-id">
        <img class="hh-por" id="hh-por" alt="">
        <div>
          <div class="hh-name" id="hh-name"></div>
          <div class="hh-sub" id="h-sub"></div>
        </div>
      </div>
      <div class="hh-stats" id="hh-stats"></div>
      <div class="hh-draft">
        <div class="team radiant" id="sb-radiant"></div>
        <span class="vs">VS</span>
        <div class="team dire" id="sb-dire"></div>
      </div>
    </div>
  </header>

  <main class="wrap">
    <section class="verdict" id="verdict"></section>
    <div class="habit" id="habit"></div>

    <section class="sec" id="review-sec">
      <div class="sec-head">
        <h2>The review</h2>
        <span class="hint">What held up, and what cost you</span>
      </div>
      <div class="cols" id="review"></div>
      <div class="card itemcard sec" id="dmgreview" style="margin-top:18px"></div>
      <div class="card itemcard sec" id="itemreview" style="margin-top:18px"></div>
    </section>

    <section class="sec">
      <div class="sec-head">
        <h2>Death review</h2>
        <span class="hint">Pick a death, then scrub the ten seconds before it</span>
      </div>
      <div class="strip-head">
        <div class="sort">
          <button type="button" id="sort-priority" class="on">Most important</button>
          <button type="button" id="sort-time">By time</button>
        </div>
      </div>
      <div class="strip" id="deathlist"></div>

      <div class="stage">
        <aside class="team-col" id="team-radiant"></aside>
        <div class="mapwrap">
          <div class="map-stage">
            <svg id="map" viewBox="0 0 640 640" role="img" aria-label="Minimap playback">
              <image id="map-bg" href="__MAP2_B64__" x="0" y="0" width="640" height="640"></image>
              <g id="ov"></g>
            </svg>
            <button type="button" class="map-toggle" id="map-toggle">Map: In-game</button>
          </div>
          <div class="transport">
            <button type="button" class="play" id="btn-play" aria-label="Play or pause">&#9654;</button>
            <div class="scrub">
              <input type="range" id="scrub" min="0" max="20" step="1" value="20">
              <div class="times"><span id="t-rel">-10.0s</span><strong id="t-clock">0:00</strong><span>death</span></div>
            </div>
            <div class="nav-death">
              <button type="button" id="prev" aria-label="Previous death">&lsaquo;</button>
              <span id="pos">1/1</span>
              <button type="button" id="next" aria-label="Next death">&rsaquo;</button>
              <span class="kbd">Space</span>
            </div>
          </div>
        </div>
        <aside class="team-col" id="team-dire"></aside>
      </div>

      <div class="card panel" id="panel" aria-live="polite"></div>
    </section>
  </main>
</div>

<script>
const R = __REPORT__, SPR = __SPRITES__;
const NS = "http://www.w3.org/2000/svg";
const ov = document.getElementById("ov");
let order = [], cur = -1, sortMode = "priority";
let frameIdx = 0, playing = false, playTimer = null;

function el(tag, attrs){
  const e = document.createElementNS(NS, tag);
  for (const k in attrs) e.setAttribute(k, attrs[k]);
  return e;
}
function esc(s){
  return String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
}
function pct(a,b){ if (a == null || !b) return 0; return Math.max(0, Math.min(100, 100 * a / b)); }
function clock(t){
  const s = Math.max(0, Math.floor(t));
  return Math.floor(s/60) + ":" + String(s%60).padStart(2,"0");
}
function teamColor(m){ return m.isRadiant ? "#3dca8a" : "#ef5b5b"; }

// Jump labels come from Python (`m.jump`), which resolves them against real
// cast data — a Pudge hook on you is "Hooked", not a Blink you never owned.
// Distance alone is only used to decide a jump happened at all.
const JUMP_MIN_PX = 22;
const TP_MIN_PX = 70;      // clearly beyond blink range

function marker(m){
  // Larger circular portraits + status badges above the hero.
  const g = el("g", {});
  const col = teamColor(m);
  const r = m.me ? 13 : 11;
  const cid = "c" + m.heroId + "_" + Math.random().toString(36).slice(2,7);
  const clip = el("clipPath", {id: cid});
  clip.appendChild(el("circle", {cx:m.x, cy:m.y, r:r}));
  g.appendChild(clip);
  if (m.icon){
    g.appendChild(el("image", {
      href:m.icon, x:m.x-r, y:m.y-r, width:2*r, height:2*r,
      "clip-path":`url(#${cid})`, preserveAspectRatio:"xMidYMid slice"
    }));
  } else {
    g.appendChild(el("circle", {cx:m.x, cy:m.y, r:r, fill:"#1a2230"}));
  }
  g.appendChild(el("circle", {
    cx:m.x, cy:m.y, r:r, fill:"none", stroke:"#070a0e", "stroke-width": 1.8
  }));
  g.appendChild(el("circle", {
    cx:m.x, cy:m.y, r:r, fill:"none", stroke:col, "stroke-width": 1.3
  }));
  // "You" gets a thin ring hugging the portrait instead of a big red circle.
  if (m.me){
    g.appendChild(el("circle", {
      cx:m.x, cy:m.y, r:r + 2.5, fill:"none", stroke:"#fbbf24",
      "stroke-width": 1, opacity: .95
    }));
  }
  const statuses = m.statuses || [];
  statuses.slice(0, 2).forEach((s, i) => {
    const y = m.y - r - 10 - i * 11;
    const lab = el("text", {
      x: m.x, y: y, fill: s.color || "#fbbf24",
      "font-size": 8, "font-weight": 800, "font-family": "Outfit, sans-serif",
      "text-anchor": "middle", stroke: "#070a0e", "stroke-width": 3,
      "paint-order": "stroke"
    });
    lab.textContent = s.label || s.kind;
    g.appendChild(lab);
  });
  const title = document.createElementNS(NS, "title");
  const st = statuses.map(s => s.label).join(", ");
  title.textContent = m.name + (m.isRadiant ? " · Radiant" : " · Dire")
    + (m.me ? " · you" : "")
    + (st ? " · " + st : "")
    + (m.hasBlink ? " · owns Blink" : "")
    + (m.jump ? " · " + m.jump : "");
  g.appendChild(title);
  return g;
}

function drawJump(from, to, label, isTP){
  // Unexplained jumps get a neutral line and no caption — better silent than
  // captioned with a mechanic that didn't happen.
  const col = isTP ? "#c9a227" : (label ? "#d7e6ff" : "#7c8794");
  ov.appendChild(el("line", {
    x1: from.x, y1: from.y, x2: to.x, y2: to.y,
    stroke: col, "stroke-width": isTP ? 1.8 : 1.4,
    "stroke-dasharray": isTP ? "6 4" : "3 3",
    opacity: label ? .92 : .5, "stroke-linecap": "round"
  }));
  ov.appendChild(el("circle", {cx: from.x, cy: from.y, r: 2.0, fill: col, opacity: .75}));
  if (!label) return;
  const mx = (from.x + to.x) / 2, my = (from.y + to.y) / 2;
  const lab = el("text", {
    x: mx, y: my - 5, fill: col, "font-size": 9, "font-weight": 700,
    "font-family": "Outfit, sans-serif", "text-anchor": "middle",
    stroke: "#070a0e", "stroke-width": 3, "paint-order": "stroke"
  });
  lab.textContent = label;
  ov.appendChild(lab);
}

function pathUntil(points, t){
  const pts = points.filter(p => p.t <= t + 0.05).map(p => [p.x, p.y]);
  if (pts.length < 2) return null;
  const segs = [];
  let cur = [pts[0]];
  for (let i=1;i<pts.length;i++){
    const d = Math.hypot(pts[i][0]-pts[i-1][0], pts[i][1]-pts[i-1][1]);
    // Break trail across blinks/TPs so the walking path stays readable.
    if (d > JUMP_MIN_PX){ if (cur.length>1) segs.push(cur); cur = [pts[i]]; }
    else cur.push(pts[i]);
  }
  if (cur.length>1) segs.push(cur);
  return segs;
}

function drawFrame(D, fi){
  const pb = D.playback;
  const F = pb.frames[fi];
  const marks = F.markers.map(m => Object.assign({}, m));
  const prev = fi > 0 ? pb.frames[fi-1].markers : [];
  const prevBy = Object.fromEntries(prev.map(m => [m.heroId, m]));
  for (const m of marks){
    const p = prevBy[m.heroId];
    m.jumpDist = p ? Math.hypot(m.x - p.x, m.y - p.y) : 0;
  }
  const meM = marks.find(m => m.me) || marks[0];
  ov.replaceChildren();

  const dead = new Set(F.deadBuildings);
  const sz = {tower:16, rax:13, fort:28};
  // Standing towers grant vision — draw your team's radius so "no vision"
  // deaths can be judged honestly against what you could actually see.
  for (const b of R.buildings){
    if (dead.has(b.key) || b.kind !== "tower") continue;
    if (b.isRadiant !== R.hero.isRadiant) continue;
    ov.appendChild(el("circle", {
      cx:b.x, cy:b.y, r:R.towerVisionR, fill:"#7dd3fc", opacity:.05,
      stroke:"#7dd3fc", "stroke-width":.7, "stroke-opacity":.28,
      "stroke-dasharray":"2 6"
    }));
  }
  for (const b of R.buildings){
    if (dead.has(b.key)) continue;
    const s = sz[b.kind] || 14;
    ov.appendChild(el("image", {href:SPR[b.icon], x:b.x-s/2, y:b.y-s/2, width:s, height:s}));
  }
  for (const w of F.wards){
    const kind = w.kind || "observer";
    // Colour carries the team (blue = yours, red = theirs); shape carries the
    // type (eye = observer, oval = sentry). No extra dot needed.
    const mine = w.isRadiant === R.hero.isRadiant;
    const icon = `ward_${kind === "sentry" ? "sentry" : "observer"}_${mine ? "ally" : "enemy"}.png`;
    const href = SPR[icon];
    // Glyphs are 35x27 — keep that aspect so the eye reads as an eye.
    const gw = kind === "sentry" ? 16 : 18;
    const gh = Math.round(gw * 27 / 35);
    if (href){
      ov.appendChild(el("image", {
        href, x: w.x - gw/2, y: w.y - gh/2, width: gw, height: gh, opacity: 0.95
      }));
    } else {
      const col = kind === "sentry" ? "#7dd3fc" : (w.isRadiant ? "#e0b83a" : "#b56fd4");
      ov.appendChild(el("circle", {cx:w.x, cy:w.y, r:3.6, fill:col, stroke:"#000", "stroke-width":.8}));
    }
  }

  const segs = pathUntil(pb.mePath || [], F.t) || [];
  for (const s of segs){
    const d = s.map((p,i)=>(i?"L":"M")+p[0]+" "+p[1]).join(" ");
    ov.appendChild(el("path", {
      d, fill:"none", stroke:"#f0b429", "stroke-width":1.4,
      opacity:.55, "stroke-linecap":"round"
    }));
  }

  for (const m of marks){
    if (!m.jumpDist || m.jumpDist < JUMP_MIN_PX) continue;
    const p = prevBy[m.heroId];
    if (p) drawJump(p, m, m.jump || null, m.jumpDist >= TP_MIN_PX);
  }

  // Spell casts — a brief icon that pops above the caster and fades. No rings,
  // no "ULT" label: the icon already says which spell it was.
  const CAST_SHOW = 0.9;                       // seconds visible
  for (const u of (pb.casts || [])){
    const age = F.t - u.t;
    if (age < -0.05 || age > CAST_SHOW) continue;
    const fade = 1 - age / CAST_SHOW;          // 1 at cast -> 0 when gone
    const live = marks.find(m => m.heroId === u.heroId);
    const cx = live ? live.x : u.x;
    const cy = live ? live.y : u.y;
    if (!u.icon) continue;
    const s = (u.ult ? 20 : 16) * (0.75 + 0.25 * fade);   // small pop-in
    const g = el("g", {opacity: Math.min(1, 0.35 + fade)});
    g.appendChild(el("rect", {
      x: cx - s/2 - 1.5, y: cy - 30 - s/2 - 1.5, width: s + 3, height: s + 3,
      rx: 4, fill: "#070a0e", opacity: 0.75
    }));
    g.appendChild(el("image", {
      href: u.icon, x: cx - s/2, y: cy - 30 - s/2, width: s, height: s
    }));
    if (u.ult){                                 // thin gold edge marks an ult
      g.appendChild(el("rect", {
        x: cx - s/2 - 1.5, y: cy - 30 - s/2 - 1.5, width: s + 3, height: s + 3,
        rx: 4, fill: "none", stroke: "#fbbf24", "stroke-width": 1.2
      }));
    }
    const t = document.createElementNS(NS, "title");
    t.textContent = `${u.hero}: ${u.name}`;
    g.appendChild(t);
    ov.appendChild(g);
  }

  for (const m of marks) if (!m.me) ov.appendChild(marker(m));
  if (meM) ov.appendChild(marker(meM));

  paintSpectator(D, F);

  const aliveIds = new Set(F.markers.map(m => m.heroId));
  const killerIds = new Set(F.markers.filter(m => m.killer).map(m => m.heroId));
  document.querySelectorAll(".slot").forEach(node => {
    const id = +node.dataset.heroId;
    node.classList.toggle("dead", !aliveIds.has(id));
    node.classList.toggle("killer", killerIds.has(id));
  });

  document.getElementById("t-rel").textContent = (F.rel >= 0 ? "+" : "") + F.rel.toFixed(1) + "s";
  document.getElementById("t-clock").textContent = clock(F.t);
  document.getElementById("scrub").value = String(fi);
}

// Spectator rail: every hero's live level / hp / mana / statuses at the current
// scrubber position, plus their items at the moment of death.
function specRow(h, m, items){
  const side = h.isRadiant ? "radiant" : "dire";
  const cls = [side, h.me ? "me" : "", m ? "" : "dead"].filter(Boolean).join(" ");
  // Live values at the scrubbed moment. h.stats holds END-OF-MATCH totals and
  // must never be shown against a timestamp — 172 CS at 1:19 is how that reads.
  const L = (m && m.live) || null;
  const head = `<div class="hd">
      ${h.icon?`<img class="hp" src="${esc(h.icon)}" alt="">`:"<div></div>"}
      <div><div class="nm">${h.pos?`<b class="po">${h.pos}</b>`:""}${esc(h.name)}</div>
        <div class="kda">${L ? `${L.k}/${L.d}/${L.a}` : "&mdash;"}</div></div>
      <span class="lvl">${m ? "L"+(m.level ?? "?") : "dead"}</span>
    </div>`;
  if (!m) return `<div class="srow ${cls}">${head}</div>`;
  const up = m.up || {};
  const badges = (up.scepter?`<span class="upg ag" title="Aghanim's Scepter">A</span>`:"")
               + (up.shard?`<span class="upg sh" title="Aghanim's Shard">S</span>`:"");
  const kit = (R.kits || {})[h.heroId] || [];
  const abs = kit.map((a, i) => {
    const cd = (m.ab || [])[i];
    if (cd === -2) return "";              // scepter/shard skill they don't own
    const state = cd === -1 ? "locked" : cd > 0 ? "cool" : "up";
    return `<span class="ab ${state}" title="${esc(a.name || "")}${
        cd > 0 ? " — "+cd+"s" : cd === -1 ? " — not skilled" : " — ready"}">
      ${a.icon?`<img src="${esc(a.icon)}" alt="">`:""}
      ${cd > 0 ? `<i>${cd}</i>` : ""}</span>`;
  }).join("");
  const slots = [];
  for (let i=0;i<6;i++){
    const it = (items||[])[i];
    slots.push(it && it.icon
      ? `<img src="${esc(it.icon)}" title="${esc(it.name)}" alt="">`
      : `<div class="sl"></div>`);
  }
  const st = (m.statuses || []).slice(0,3).map(x =>
    `<span style="color:${esc(x.color)}">${esc(x.label)}</span>`).join("");
  return `<div class="srow ${cls}">
    ${head}
    ${(abs||badges)?`<div class="abs">${abs}${badges}</div>`:""}
    <div class="bars">
      <div class="sbar"><i style="width:${pct(m.hp,m.maxHp)}%;background:#4ade80"></i></div>
      <div class="sbar"><i style="width:${pct(m.mp,m.maxMp)}%;background:#3d9be9"></i></div>
    </div>
    ${L ? `<div class="num"><span>${L.cs ?? 0} cs</span><span>${
       L.gold != null ? (L.gold/1000).toFixed(1)+"k net" : ""}</span></div>` : ""}
    <div class="its">${slots.join("")}</div>
    ${st?`<div class="st">${st}</div>`:""}
  </div>`;
}

function paintSpectator(D, F){
  const rad = document.getElementById("team-radiant");
  const dire = document.getElementById("team-dire");
  if (!rad || !dire) return;
  const itemsBy = {};
  for (const row of (D.allyLoadouts || []).concat(D.enemyLoadouts || [])){
    itemsBy[row.heroId] = row.items || [];
  }
  // The player themselves is in neither loadout list — their inventory is
  // D.items — so without this their own row showed six empty slots.
  itemsBy[R.hero.heroId] = D.items || [];
  const byId = Object.fromEntries(F.markers.map(m => [m.heroId, m]));
  const draw = (box, list, label, colour) => {
    box.innerHTML = `<h3 style="color:${colour}">${label}</h3>` +
      list.map(h => specRow(h, byId[h.heroId], itemsBy[h.heroId])).join("");
  };
  draw(rad, R.scoreboard.radiant, "Radiant", "var(--radiant)");
  draw(dire, R.scoreboard.dire, "Dire", "var(--dire)");
}

function stopPlay(){
  playing = false;
  if (playTimer){ clearInterval(playTimer); playTimer = null; }
  document.getElementById("btn-play").textContent = "▶";
}
function startPlay(){
  const D = R.deaths[cur];
  if (!D) return;
  playing = true;
  document.getElementById("btn-play").textContent = "❚❚";
  if (frameIdx >= D.playback.frames.length - 1) frameIdx = 0;
  playTimer = setInterval(() => {
    frameIdx += 1;
    if (frameIdx >= D.playback.frames.length){
      frameIdx = D.playback.frames.length - 1;
      drawFrame(D, frameIdx);
      stopPlay();
      return;
    }
    drawFrame(D, frameIdx);
    const meM = D.playback.frames[frameIdx].markers.find(m => m.me);
    const box = document.getElementById("live-status");
    if (box && meM){
      const sts = meM.statuses || [];
      box.innerHTML = sts.length
        ? `<div class="statuses">${sts.map(s =>
            `<span class="stchip ${esc(s.polarity||"bad")}" style="color:${esc(s.color)}">${esc(s.label)}<span class="src">${esc(s.source)}${s.rem!=null?` · ${s.rem}s`:""}</span></span>`
          ).join("")}</div>`
        : `<span class="chip">No buffs or hard CC on you at this moment</span>`;
    }
  }, Math.max(80, D.playback.step * 1000));
}

function rebuildOrder(){
  const idxs = R.deaths.map((_,i)=>i);
  if (sortMode === "time") idxs.sort((a,b)=>R.deaths[a].time-R.deaths[b].time);
  else idxs.sort((a,b)=>(R.deaths[b].score-R.deaths[a].score)||(R.deaths[a].time-R.deaths[b].time));
  order = idxs;
  paintList();
}

function paintList(){
  const nav = document.getElementById("deathlist");
  nav.innerHTML = "";
  order.forEach(i => {
    const d = R.deaths[i];
    const b = document.createElement("button");
    b.type = "button";
    b.className = "dcard" + (i === cur ? " on" : "");
    const inHabit = (R.habit && R.habit.headline && (R.habit.headline.deaths||[]).includes(d.idx));
    b.innerHTML =
      `<div class="row1"><span class="dot ${esc(d.severity.key)}"></span>
         <span class="clock">${esc(d.clock)}</span>
         <span class="num">#${d.idx+1}</span></div>
       <div class="title">${esc(d.title)}</div>
       ${inHabit ? `<span class="flag">pattern</span>` : ""}`;
    b.onclick = () => show(i);
    nav.appendChild(b);
  });
}

/* The takeaway, above everything else: verdict, then strengths/mistakes,
   then itemisation. An average reader should be able to stop after this. */
function paintReview(){
  const H = R.hero, S = R.summary, CO = R.coach;
  const por = document.getElementById("hh-por");
  if (H.icon) por.src = H.icon; else por.remove();
  const result = H.won == null ? "" : H.won
    ? `<span class="badge win">Victory</span>` : `<span class="badge loss">Defeat</span>`;
  document.getElementById("hh-name").innerHTML = `${esc(H.name)} ${result}`;
  document.getElementById("h-sub").textContent =
    `${H.side} · match ${R.matchId}`;

  const meRow = R.scoreboard.radiant.concat(R.scoreboard.dire).find(p => p.me) || {};
  const st = meRow.stats || {};
  const stat = (v, label) => v == null ? "" :
    `<div class="hh-stat"><b>${esc(String(v))}</b><span>${esc(label)}</span></div>`;
  document.getElementById("hh-stats").innerHTML =
    stat(`${st.k ?? 0}/${st.d ?? 0}/${st.a ?? 0}`, "K/D/A")
    + stat(st.cs, "last hits") + stat(st.gpm, "GPM") + stat(st.xpm, "XPM")
    + stat(S.deathCount, "deaths");

  const V = document.getElementById("verdict");
  if (CO && CO.headline){
    V.innerHTML = `<div class="eyebrow">Your one habit this match</div>
      <h1>${esc(CO.headline)}</h1>
      ${CO.fix ? `<p class="fix">${esc(CO.fix)}</p>` : ""}
      ${CO.overall ? `<p class="overall">${esc(CO.overall)}</p>` : ""}
      ${CO.drill ? `<div class="drill"><b>Next game:</b><span>${esc(CO.drill)}</span></div>` : ""}`;
  } else {
    // No AI pass — fall back to the measured habit so the page still leads
    // with a conclusion rather than a wall of deaths.
    const hb = R.habit && R.habit.headline;
    V.innerHTML = hb
      ? `<div class="eyebrow">Your one habit this match</div>
         <h1>${esc(hb.title)}</h1><p class="fix">${esc(hb.text)}</p>`
      : `<div class="eyebrow">Match review</div>
         <h1>${S.deathCount} deaths, ${S.totalGoldLost} gold handed over</h1>
         <p class="fix">Pick a death below to see what happened in the ten seconds before it.</p>`;
    if (!hb && !S.deathCount) V.style.display = "none";
  }

  const pts = (arr) => (arr || []).map(p =>
    `<div class="pt"><p class="ptt">${esc(p.title)}</p><p class="ptx">${esc(p.detail)}</p></div>`).join("");
  const rev = document.getElementById("review");
  const good = CO && CO.did_well && CO.did_well.length;
  const bad  = CO && CO.mistakes && CO.mistakes.length;
  rev.innerHTML =
    (good ? `<div class="card colcard good"><h3><span class="ic">&#10003;</span>What held up</h3>
       ${pts(CO.did_well)}</div>` : "")
    + (bad ? `<div class="card colcard bad"><h3><span class="ic">&#33;</span>What cost you</h3>
       ${pts(CO.mistakes)}</div>` : "");
  const DM = R.damage;
  const dmgEl = document.getElementById("dmgreview");
  if (DM && DM.sources && DM.sources.length){
    const COL = {magical:"#60a5fa",physical:"#fbbf24",pure:"#f472b6",unknown:"#94a3b8"};
    const bar = DM.split.map(s =>
      `<i style="width:${s.pct}%;background:${COL[s.type]||COL.unknown}" title="${s.type} ${s.pct}%"></i>`).join("");
    const legend = DM.split.map(s =>
      `<span class="dlg"><b style="background:${COL[s.type]||COL.unknown}"></b>${s.type} ${s.pct}%</span>`).join("");
    const max = DM.sources[0].damage || 1;
    const rows = DM.sources.map(r => `
      <div class="drow2">
        ${r.icon?`<img src="${esc(r.icon)}" alt="">`:`<div class="dic">&#9876;</div>`}
        <div><div class="dn">${esc(r.name)}</div>
          <div class="dbar"><i style="width:${Math.round(100*r.damage/max)}%;
            background:${COL[r.type]||COL.unknown}"></i></div></div>
        <div class="dv">${r.damage.toLocaleString()}</div>
      </div>`).join("");
    const heroes = (DM.heroes||[]).map(h => `
      <div class="dhero" title="${esc(h.name)}">
        ${h.icon?`<img src="${esc(h.icon)}" alt="">`:""}
        <div><b>${esc(h.name)}</b><span>${h.damage.toLocaleString()} dmg${
          h.kills?` · ${h.kills} kill${h.kills>1?"s":""}`:""}</span></div></div>`).join("");
    dmgEl.innerHTML = `<div class="eyebrow">What actually damages you</div>
      <div class="dsplit">${bar}</div><div class="dlegend">${legend}</div>
      <div class="dcols"><div>${rows}</div><div class="dheroes">${heroes}</div></div>
      <p class="hint">Whole-match totals. OpenDota reports damage by inflictor for the
        match, not per death, so this is a pattern — not a breakdown of any one death.</p>`;
  } else if (dmgEl) { dmgEl.remove(); }

  const item = document.getElementById("itemreview");
  if (CO && CO.itemization){
    item.innerHTML = `<div class="eyebrow">Itemisation</div><p>${esc(CO.itemization)}</p>`;
  } else { item.remove(); }
  if (!good && !bad) document.getElementById("review-sec").style.display = "none";
}

function paintBoard(){
  const paint = (elId, rows) => {
    document.getElementById(elId).innerHTML = rows.map(h => `
      <div class="slot ${h.isRadiant?"radiant":"dire"}${h.me?" me":""}" data-hero-id="${h.heroId}" title="${esc(h.name)}${h.pos?" · pos "+h.pos:""}">
        ${h.icon ? `<img src="${esc(h.icon)}" alt="${esc(h.name)}">` : ""}
      </div>`).join("");
  };
  paint("sb-radiant", R.scoreboard.radiant);
  paint("sb-dire", R.scoreboard.dire);
  paintReview();

  const hb = R.habit && R.habit.headline;
  const nem = R.habit && R.habit.nemesis;
  const habitEl = document.getElementById("habit");
  if (!nem && !(hb && R.coach)){ habitEl.remove(); return; }
  const nemIcon = nem ? (R.scoreboard.radiant.concat(R.scoreboard.dire)
    .find(p => p.heroId === nem.heroId) || {}).icon : null;
  habitEl.innerHTML =
    (hb && R.coach ? `<span><b>Measured pattern:</b> ${esc(hb.text)}</span>` : "")
    + (nem ? `<span class="nem" style="margin-left:auto">
        ${nemIcon?`<img src="${esc(nemIcon)}" alt="">`:""}
        <b>${esc(nem.name)}</b> killed you ${nem.count}&times;</span>` : "");
}

function paintPanel(D, meM){
  const assistIcons = (D.killedBy && D.killedBy.assistHeroes || []).map(h =>
    h.icon ? `<img src="${esc(h.icon)}" title="${esc(h.name)}" alt="" style="width:22px;height:22px;border-radius:50%;border:1.5px solid #ef5b5b;margin-left:4px">` : ""
  ).join("");
  const kb = D.killedBy
    ? `<div class="kb">${D.killedBy.icon?`<img src="${esc(D.killedBy.icon)}" alt="">`:""}
        <div>Killed by <b>${esc(D.killedBy.name)}</b>${D.killedBy.assists?` · +${D.killedBy.assists}`:""}
        ${assistIcons?`<div style="margin-top:6px;display:flex;flex-wrap:wrap">${assistIcons}</div>`:""}</div></div>`
    : `<div class="kb"><div>Killed by creeps or tower</div></div>`;
  const meters = meM && meM.maxHp ? `
    <div class="meters">
      <div class="meter"><div class="lbl">Health</div><div class="val">${meM.hp}/${meM.maxHp}</div>
        <div class="bar"><i style="width:${pct(meM.hp,meM.maxHp)}%;background:#4caf50"></i></div></div>
      <div class="meter"><div class="lbl">Mana</div><div class="val">${meM.mp}/${meM.maxMp}</div>
        <div class="bar"><i style="width:${pct(meM.mp,meM.maxMp)}%;background:#3d9be9"></i></div></div>
    </div><div class="hint">At / near death</div>` : "";

  const findingCard = f => `
    <div class="finding ${esc(f.tone)}">
      <div class="ft">${esc(f.title)}</div>
      <div class="fx">${esc(f.text)}</div>
    </div>`;
  const wrong = (D.findings||[]).filter(f => f.tone === "bad" || f.tone === "warn");
  const helped = (D.findings||[]).filter(f => f.tone === "ok");
  const wrongHtml = wrong.length
    ? `<div class="findings">${wrong.map(findingCard).join("")}</div>`
    : `<span class="chip">Nothing flagged as a clear mistake</span>`;
  const helpedHtml = helped.length
    ? `<div class="findings">${helped.map(findingCard).join("")}</div>`
    : `<span class="chip">No clear positives this death</span>`;

  const inv = (D.items||[]).map(it => it.icon
    ? `<img src="${esc(it.icon)}" title="${esc(it.name)}" alt="${esc(it.name)}" loading="lazy">`
    : `<div class="ph">${esc(it.name)}</div>`).join("")
    + (D.neutral ? (D.neutral.icon
      ? `<img class="neu" src="${esc(D.neutral.icon)}" title="${esc(D.neutral.name)}" alt="" loading="lazy">`
      : `<div class="ph neu">${esc(D.neutral.name)}</div>`) : "");

  const loadoutBlock = (rows, ally) => (rows || []).map(e => {
    const flags = [
      e.hasBlink ? "Blink" : null,
      e.hasForce ? "Force" : null,
      e.involved ? "kill credit" : null,
    ].filter(Boolean).join(" · ");
    const its = (e.items || []).map(it => it.icon
      ? `<img src="${esc(it.icon)}" title="${esc(it.name)}" alt="${esc(it.name)}" loading="lazy">`
      : `<div class="ph">${esc(it.name)}</div>`).join("")
      || `<span class="chip">No major items yet</span>`;
    return `<div class="erow${ally ? " ally" : ""}">
      <div class="etop">
        ${e.icon ? `<img class="hero" src="${esc(e.icon)}" alt="">` : ""}
        <span class="ename">${esc(e.name)}</span>
        ${flags ? `<span class="eflags">${esc(flags)}</span>` : ""}
      </div>
      <div class="eitems">${its}</div>
    </div>`;
  }).join("");
  const enemyLoadouts = loadoutBlock(D.enemyLoadouts, false);
  const allyLoadouts = loadoutBlock(D.allyLoadouts, true);

  const ultCard = (u, label) => {
    if (!u) return "";
    const st = u.state || (u.ready ? "ready" : "cooldown");
    return `<div class="ult">
      ${u.icon || u.heroIcon ? `<img src="${esc(u.icon || u.heroIcon)}" alt="">` : `<div></div>`}
      <div>
        <div class="un">${esc(label || u.hero || "You")} · ${esc(u.name)}</div>
        <div class="ux">${esc(u.text)}</div>
      </div>
      <div class="ustate ${esc(st)}">${esc(st)}</div>
    </div>`;
  };
  const ultBlock = [
    ultCard(D.myUlt, "You"),
    ...(D.enemyUlts || []).map(u => ultCard(u, u.hero)),
  ].filter(Boolean).join("") || `<span class="chip">No ultimate data</span>`;

  const casts = D.casts || [];
  // Prefer ultimates + mobility items first; keep the list short.
  const castPriority = (c) => (c.ult ? 0 : c.kind === "item" ? 1 : 2);
  const castShown = casts.slice().sort((a,b) => castPriority(a) - castPriority(b) || a.t - b.t);
  const castChips = castShown.length
    ? `<div class="casts">${castShown.map(c => {
        const tm = `${c.rel > 0 ? "+" : ""}${c.rel}s`;
        const who = c.hero ? `<span class="who">${esc((c.hero||"").split(" ").pop())}</span>` : "";
        const ic = c.icon ? `<img src="${esc(c.icon)}" alt="">` : "";
        return `<span class="tchip${c.ult ? " ult" : ""}" title="${esc(c.hero||"")} · ${esc(c.name)}">${ic}<span class="tm">${tm}</span>${esc(c.name)}${who}</span>`;
      }).join("")}</div>`
    : `<span class="chip">No major casts in the last 10s</span>`;
  const chips = (D.chips||[]).map(c => `<span class="chip">${esc(c.t)}</span>`).join("");
  const counts = D.counts || {};
  const roster = (D.roster||[]).map(m => {
    const side = m.isRadiant ? "Radiant" : "Dire";
    const cls = (m.me ? "me " : "") + (m.isRadiant ? "radiant" : "dire");
    const dist = m.me ? "death spot" : `~${m.dist} units`;
    const hp = (m.hp != null && m.maxHp) ? ` · ${Math.round(100*m.hp/m.maxHp)}% HP` : "";
    const kill = m.killer ? `<span class="tagkill">kill credit</span>` : "";
    return `<div class="rrow ${cls}">
      ${m.icon?`<img src="${esc(m.icon)}" alt="">`:`<div></div>`}
      <div><div class="rn">${esc(m.name)}${kill}</div>
      <div class="rm">${m.me?"You":side}${m.level!=null?` · lvl ${m.level}`:""}${hp}</div></div>
      <div class="rd">${esc(dist)}</div></div>`;
  }).join("");

  const myStatuses = (meM && meM.statuses) || [];
  const statusHtml = myStatuses.length
    ? `<div class="statuses">${myStatuses.map(s =>
        `<span class="stchip ${esc(s.polarity||"bad")}" style="color:${esc(s.color)}">${esc(s.label)}<span class="src">${esc(s.source)}${s.rem!=null?` · ${s.rem}s`:""}</span></span>`
      ).join("")}</div>`
    : `<span class="chip">No buffs or hard CC on you at this moment</span>`;

  const focusItems = [];
  if (D.tip) focusItems.push(D.tip);
  if (wrong[0]) {
    const line = `Watch for “${wrong[0].title}” — ${wrong[0].text}`;
    if (line !== D.tip) focusItems.push(line);
  }
  const focusHtml = focusItems.slice(0, 2).map((t, i) =>
    `<li><span class="n">${i+1}</span><span>${esc(t)}</span></li>`
  ).join("");

  document.getElementById("panel").innerHTML = `
    <div class="sev-tag ${esc(D.severity.key)}">${esc(D.severity.label)}</div>
    <h1>${esc(D.title)}</h1>
    <p class="blurb">${esc(D.blurb)} <span style="color:var(--faint)">· ${esc(D.clock)} · death #${D.idx+1}</span></p>
    ${chips ? `<div class="chips" style="margin:0 0 18px">${chips}</div>` : ""}
    ${D.diagnosis ? `<div class="diag ${esc(D.diagnosis.kind)}">
      <span class="k">${D.diagnosis.kind === "awareness" ? "Awareness miss"
        : D.diagnosis.kind === "execution" ? "Execution miss" : "Fight commitment"}</span>
      <p class="t">${esc(D.diagnosis.title)}</p>
      <p class="x">${esc(D.diagnosis.text)}</p>
    </div>` : ""}
    ${D.aiAdvice ? `<div class="coach"><span class="k">Coach · do this instead</span>
      <p class="x" style="margin:0">${esc(D.aiAdvice)}</p></div>` : ""}
    <div class="lesson">
      <span class="k">Key lesson</span>
      <p class="t">${esc(D.tip)}</p>
      <p class="s">Fix this pattern and similar deaths get rarer.</p>
    </div>
    ${D.story ? `<div class="story"><span class="k">Lead-up</span>${esc(D.story)}${D.situation ? `<div style="margin-top:8px;color:#aebccb">${esc(D.situation)}</div>` : ""}</div>` : ""}
    <div class="section"><h3>What went wrong</h3>${wrongHtml}</div>
    <div class="section"><h3>What helped</h3>${helpedHtml}</div>
    <div class="section"><h3>When you died</h3>
      ${kb}
      <div style="margin-top:12px">${meters}</div>
      ${meM && meM.level != null ? `<div class="lvl" style="margin-top:10px;height:44px">Level ${meM.level}</div>`:""}
      <div style="margin-top:12px" id="live-status">${statusHtml}</div>
    </div>
    <details class="sec"><summary>Who was here · ${counts.allies||0} allies · ${counts.enemies||0} enemies</summary>
      <div class="inner"><div class="roster">${roster}</div></div></details>
    <details class="sec"><summary>Items at that moment</summary><div class="inner">
      <div class="subh">Your items</div><div class="items">${inv||`<span class="chip">No notable items yet at this point</span>`}</div>
      <div class="subh">Allies</div><div class="eload">${allyLoadouts||`<span class="chip">No allies</span>`}</div>
      <div class="subh">Enemies</div><div class="eload">${enemyLoadouts||`<span class="chip">No enemies</span>`}</div>
      <div class="hint">Estimated from purchase logs — consumed or sold items may still show.</div>
    </div></details>
    <details class="sec"><summary>Ultimates at death</summary>
      <div class="inner"><div class="ults">${ultBlock}</div>
      <div class="hint">Cooldowns estimated from cast times and level — approximate.</div></div></details>
    <details class="sec"><summary>Casts · last 10 seconds</summary>
      <div class="inner">${castChips}</div></details>
    <div class="next"><span class="k">Next focus</span><ol>${focusHtml}</ol></div>
    <div class="leg">
      <span class="sw" style="background:#3dca8a;margin-left:0"></span>Radiant
      <span class="sw" style="background:#ef5b5b"></span>Dire
      <br>Play the last 10s · labels above heroes = status · gold burst = ult · cyan ping = item use · Space toggles play
      <br>Status labels are inferred from cast timings, not game modifiers — treat as approximate.
    </div>`;
}

function paintPos(){
  const n = R.deaths.length, place = order.indexOf(cur)+1;
  document.getElementById("pos").textContent = `${place}/${n}`;
  document.getElementById("prev").disabled = place <= 1;
  document.getElementById("next").disabled = place >= n;
}

function show(i){
  stopPlay();
  cur = i;
  const D = R.deaths[i];
  const scrub = document.getElementById("scrub");
  scrub.max = String(D.playback.frames.length - 1);
  frameIdx = D.playback.frames.length - 1;
  scrub.value = String(frameIdx);
  drawFrame(D, frameIdx);
  const meM = (D.playback.frames[frameIdx].markers.find(m => m.me)
            || D.markers.find(m => m.me) || D.markers[0]);
  paintList();
  paintPanel(D, meM);
  paintPos();
}

function step(delta){
  const at = order.indexOf(cur), next = at + delta;
  if (next < 0 || next >= order.length) return;
  show(order[next]);
}

document.getElementById("sort-priority").onclick = () => {
  sortMode = "priority";
  document.getElementById("sort-priority").classList.add("on");
  document.getElementById("sort-time").classList.remove("on");
  const keep = cur; rebuildOrder(); show(keep);
};
document.getElementById("sort-time").onclick = () => {
  sortMode = "time";
  document.getElementById("sort-time").classList.add("on");
  document.getElementById("sort-priority").classList.remove("on");
  const keep = cur; rebuildOrder(); show(keep);
};
// Both maps are full-frame: grid 54..202 spans the whole image.
//
// This was previously scaled by 0.8969 from cross-correlating the two map
// images, which was wrong and caused visible drift. The geometry settles it:
// DotaMiniMap renders world [-8600, 8600] with a 0.051 inset, and grid 54..202
// at ~129.5 world-units per cell is world ±9583 — which lands at fractions
// 0.000..1.000 of the image. Edge-to-edge, same as the dark map.
const MB = 640;
const MAP_CFG = {
  ingame: {href:"__MAP2_B64__", x:0, y:0, w:MB, h:MB, label:"Map: In-game"},
  dark:   {href:"__MAP_B64__",  x:0, y:0, w:MB, h:MB, label:"Map: Dark"},
};
let mapStyle = "ingame";
try { mapStyle = localStorage.getItem("rc-map") || "ingame"; } catch(e){}
function applyMapStyle(){
  const c = MAP_CFG[mapStyle] || MAP_CFG.ingame;
  const bg = document.getElementById("map-bg");
  bg.setAttribute("href", c.href);
  bg.setAttribute("x", c.x); bg.setAttribute("y", c.y);
  bg.setAttribute("width", c.w); bg.setAttribute("height", c.h);
  document.getElementById("map-toggle").textContent = c.label;
}
document.getElementById("map-toggle").onclick = () => {
  mapStyle = mapStyle === "ingame" ? "dark" : "ingame";
  try { localStorage.setItem("rc-map", mapStyle); } catch(e){}
  applyMapStyle();
};
applyMapStyle();

document.getElementById("prev").onclick = () => step(-1);
document.getElementById("next").onclick = () => step(1);
document.getElementById("btn-play").onclick = () => playing ? stopPlay() : startPlay();
document.getElementById("scrub").oninput = (e) => {
  stopPlay();
  frameIdx = +e.target.value;
  const D = R.deaths[cur];
  drawFrame(D, frameIdx);
  const meM = (D.playback.frames[frameIdx].markers.find(m => m.me)
            || D.markers.find(m => m.me));
  const box = document.getElementById("live-status");
  if (box && meM){
    const sts = meM.statuses || [];
    box.innerHTML = sts.length
      ? `<div class="statuses">${sts.map(s =>
          `<span class="stchip ${esc(s.polarity||"bad")}" style="color:${esc(s.color)}">${esc(s.label)}<span class="src">${esc(s.source)}${s.rem!=null?` · ${s.rem}s`:""}</span></span>`
        ).join("")}</div>`
      : `<span class="chip">No buffs or hard CC on you at this moment</span>`;
  }
};
window.addEventListener("keydown", (e) => {
  if (e.key === "ArrowLeft"){ e.preventDefault(); step(-1); }
  if (e.key === "ArrowRight"){ e.preventDefault(); step(1); }
  if (e.key === " " || e.code === "Space"){
    e.preventDefault();
    playing ? stopPlay() : startPlay();
  }
});

paintBoard();
rebuildOrder();
show(__DEFAULT__);
</script>
</body></html>
"""
