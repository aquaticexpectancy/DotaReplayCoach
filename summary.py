"""Whole-match fact sheet — the positives as well as the deaths.

Everything here is measured, including the percentiles: OpenDota publishes
hero-specific benchmarks per match, so "92nd percentile GPM" is a real number
rather than a guess. Nothing in this module estimates or infers.
"""
from __future__ import annotations

import heroes as heroes_mod
import items as items_mod

# Benchmarks worth showing. hero_healing is deliberately excluded: a hero who
# heals zero scores a meaningless ~98th percentile on it.
_BENCH_LABEL = {
    "gold_per_min": "Gold per minute",
    "xp_per_min": "XP per minute",
    "last_hits_per_min": "Last hits per minute",
    "hero_damage_per_min": "Hero damage per minute",
    "tower_damage": "Tower damage",
    "kills_per_min": "Kills per minute",
    "stuns_per_min": "Stun duration per minute",
}
# Items too cheap/common to be worth a build timeline entry.
_MINOR = {"tango", "clarity", "flask", "branches", "circlet", "slippers", "gauntlets",
          "mantle", "faerie_fire", "enchanted_mango", "ward_observer", "ward_sentry",
          "tpscroll", "quelling_blade", "stout_shield", "recipe", "boots",
          "ring_of_protection", "blight_stone", "orb_of_venom", "magic_stick"}


def _mmss(t) -> str:
    t = int(t or 0)
    return f"{t // 60}:{t % 60:02d}"


def _od_player(od: dict, account_id: int) -> tuple[int, dict] | tuple[None, None]:
    for i, p in enumerate(od.get("players") or []):
        if p.get("account_id") == account_id:
            return i, p
    return None, None


def build(od: dict, match, me, analyses) -> dict:
    """Assemble the measured whole-match picture for one player."""
    idx, p = _od_player(od, me.account_id)
    if p is None:
        return {}
    hero = heroes_mod.load()
    item_db = items_mod.load()
    hname = lambda h: hero.get(str(h), {}).get("name", str(h))
    dur = od.get("duration") or 0
    won = bool(od.get("radiant_win")) == bool(p.get("isRadiant"))

    # --- benchmarks: real hero-specific percentiles -------------------------
    bench = []
    for key, row in (p.get("benchmarks") or {}).items():
        if key not in _BENCH_LABEL or not isinstance(row, dict):
            continue
        pct = row.get("pct")
        raw = row.get("raw")
        if pct is None or raw is None:
            continue
        bench.append({"metric": _BENCH_LABEL[key],
                      "value": round(raw, 1) if isinstance(raw, float) else raw,
                      "percentile": round(pct * 100)})
    bench.sort(key=lambda b: b["percentile"], reverse=True)

    # --- item build timeline ------------------------------------------------
    by_key = items_mod.load_by_key()
    first_buy, order = {}, []
    for ev in (p.get("purchase_log") or []):
        k, t = ev.get("key"), ev.get("time")
        if not k or t is None or k in _MINOR or k.startswith("recipe_"):
            continue
        if (by_key.get(k, {}).get("cost") or 0) < 600:
            continue
        if k not in first_buy:
            first_buy[k] = t
            order.append(k)
    # Drop components once their upgrade was also bought, so the timeline reads
    # "Sange and Yasha", not Claymore + Blade of Alacrity + Yasha + Sange.
    kept = set(items_mod.collapse_components(order, by_key))
    build_order = [{"item": by_key.get(k, {}).get("name") or k, "at": _mmss(first_buy[k])}
                   for k in order if k in kept]

    # --- teamfight participation -------------------------------------------
    fights = od.get("teamfights") or []
    joined = fought_and_died = 0
    for tf in fights:
        row = (tf.get("players") or [{}])[idx] if idx < len(tf.get("players") or []) else {}
        took_part = (row.get("damage") or 0) > 0 or (row.get("deaths") or 0) > 0
        if took_part:
            joined += 1
            if row.get("deaths"):
                fought_and_died += 1

    # --- buildings this player personally destroyed -------------------------
    npc = hero.get(str(me.hero_id), {}).get("npc") or ""
    my_buildings = [
        {"at": _mmss(o.get("time")), "what": (o.get("key") or "").replace("npc_dota_", "")}
        for o in (od.get("objectives") or [])
        if o.get("type") == "building_kill" and npc and o.get("unit") == npc
    ]

    # --- deaths, condensed --------------------------------------------------
    deaths = [{"at": _mmss(a.time), "label": a.label,
               "killed_by": hname(a.death.killer) if a.death and a.death.killer else "creeps/tower",
               "laning": bool(a.features.get("laning"))}
              for a in sorted(analyses, key=lambda x: x.time)]
    early = sum(1 for d in deaths if d["laning"])

    return {
        "result": "WIN" if won else "LOSS",
        "hero": hname(me.hero_id),
        "duration": _mmss(dur),
        "kda": {"k": p.get("kills"), "d": p.get("deaths"), "a": p.get("assists")},
        "economy": {
            "gpm": p.get("gold_per_min"), "xpm": p.get("xp_per_min"),
            "last_hits": p.get("last_hits"), "denies": p.get("denies"),
            "net_worth": p.get("net_worth"), "level": p.get("level"),
        },
        "damage": {"hero_damage": p.get("hero_damage"),
                   "tower_damage": p.get("tower_damage")},
        "percentiles_vs_same_hero": bench,
        "build_order": build_order,
        "teamfights": {"total": len(fights), "participated": joined,
                       "died_in": fought_and_died},
        "buildings_destroyed": my_buildings,
        "deaths": deaths,
        "deaths_in_laning": early,
        "allies": [hname(h.hero_id) for h in match.team_of(me)],
        "enemies": [hname(h.hero_id) for h in match.enemies_of(me)],
    }
