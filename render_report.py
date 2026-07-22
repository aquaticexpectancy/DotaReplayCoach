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
import abilities as abilities_mod
import status as status_mod
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
    if f["nearest_ally"] > C.ISOLATED_ALLY:
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

    if f["gankers_were_far"] >= 2:
        out.append({
            "tone": "bad",
            "title": "Unseen rotation",
            "text": (f"{f['gankers_were_far']} of them were far away just "
                     f"{C.ROTATE_LOOKBACK:.0f}s earlier — they rotated onto you."),
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
    if f.get("gankers_were_far", 0) >= 2 and f.get("enemies_near", 0) >= 2:
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
    return _LABEL_COPY.get(label, _LABEL_COPY["Death"])


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
    """Best-effort inventory at time t (purchase log → STRATZ → end items)."""
    inv = items_mod.snapshot_items(track.purchase_log, t)
    if inv:
        return inv
    main_ids, _neutral = items_at(track, t)
    if main_ids:
        return items_mod.from_item_ids(main_ids, item_db)
    return items_mod.from_item_ids(track.final_items or [], item_db)


def _ult_cast_events(match: MatchData, me: HeroTrack, t0: float, t1: float,
                     ab_meta: dict, ults: dict, hmeta) -> list[dict]:
    """Ultimate casts in [t0, t1] with map positions for playback overlays."""
    ult_by_hero = {int(hid): u for hid, u in ults.items()}
    events = []
    for tr in match.players:
        ult = ult_by_hero.get(tr.hero_id)
        if not ult:
            continue
        aid = ult["abilityId"]
        for ct, ca, _target in tr.ability_casts:
            if ca != aid:
                continue
            if ct < t0 - 0.05 or ct > t1 + 0.05:
                continue
            info = abilities_mod.info(aid, ab_meta)
            pos = pos_at(tr, ct)
            if not pos:
                continue
            x, y = P(pos.x, pos.y)
            name, hic = hmeta(tr.hero_id)
            events.append({
                "t": round(ct, 2),
                "rel": round(ct - t1, 1),
                "heroId": tr.hero_id,
                "hero": name,
                "heroIcon": hic,
                "name": info.get("name") or ult.get("name") or "Ultimate",
                "icon": info.get("icon") or ult.get("icon"),
                "x": x, "y": y,
                "isRadiant": tr.is_radiant,
                "me": tr is me,
            })
    events.sort(key=lambda e: e["t"])
    return events


def _build_playback(match: MatchData, me: HeroTrack, death_t: float,
                    death_xy: tuple[float, float], killers: set, hmeta,
                    ab_meta: dict | None = None, ults: dict | None = None,
                    item_db: dict | None = None) -> dict:
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
    ult_casts = []
    item_casts = []
    if ab_meta is not None and ults is not None:
        ult_casts = _ult_cast_events(match, me, t0, death_t, ab_meta, ults, hmeta)
    if item_db is not None:
        item_casts = status_mod.build_item_map_events(
            match, me, t0, death_t, item_db, hmeta, P)
    return {
        "t0": round(t0, 2),
        "t1": round(death_t, 2),
        "step": PLAYBACK_STEP,
        "frames": frames,
        "mePath": me_path,
        "ultCasts": ult_casts,
        "itemCasts": item_casts,
    }


def build_report(match: MatchData, me: HeroTrack, analyses: list[DeathAnalysis]) -> dict:
    hero = heroes.load()
    item = items_mod.load()
    ab_meta = abilities_mod.load_meta()
    ults = abilities_mod.load_ultimates()

    def hmeta(hid):
        h = hero.get(str(hid), {})
        return h.get("name", f"hero {hid}"), h.get("icon")

    # In-game style scoreboard order: Radiant left, Dire right.
    scoreboard = {"radiant": [], "dire": []}
    for h in match.players:
        name, ic = hmeta(h.hero_id)
        row = {
            "heroId": h.hero_id, "name": name, "icon": ic,
            "isRadiant": h.is_radiant, "me": h is me,
        }
        (scoreboard["radiant"] if h.is_radiant else scoreboard["dire"]).append(row)

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
        neutral = None
        if me.final_neutral:
            n = item.get(str(me.final_neutral), {})
            if n:
                neutral = {"name": n.get("name", f"item {me.final_neutral}"),
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
                         "a sudden gap close is a blink, not a teleport."),
            })

        my_ult = _ult_status(me, t, ab_meta, ults)
        if my_ult:
            if my_ult["state"] == "ready":
                findings.insert(0, {
                    "tone": "warn", "title": f"{my_ult['name']} ready",
                    "text": my_ult["text"] + " Check whether you should have used it.",
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
                **st,
            })
        ready_enemy_ults = [u for u in enemy_ults if u.get("state") == "ready"]
        if ready_enemy_ults:
            names = ", ".join(u["hero"] + " (" + u["name"] + ")" for u in ready_enemy_ults[:3])
            findings.insert(0, {
                "tone": "bad", "title": "Enemy ult ready",
                "text": f"Available against you: {names}.",
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
                                        ab_meta, ults, item),
        })

    vision_px = 1600 / WORLD_PER_GRID / (C.GAME_MAX - C.GAME_MIN) * M
    my_name, my_icon = hmeta(me.hero_id)
    won = (match.radiant_win == me.is_radiant) if match.radiant_win is not None else None

    focus = max(deaths, key=lambda d: d["score"]) if deaths else None
    total_gold = sum(d["cost"]["gold"] for d in deaths)
    critical_n = sum(1 for d in deaths if d["severity"]["key"] == "critical")
    notable_n = sum(1 for d in deaths if d["severity"]["key"] == "notable")
    focus_list_i = next((i for i, d in enumerate(deaths) if d is focus), 0) if focus else 0

    return {
        "matchId": match.match_id,
        "hero": {"name": my_name, "icon": my_icon, "heroId": me.hero_id,
                 "side": "Radiant" if me.is_radiant else "Dire",
                 "isRadiant": me.is_radiant, "won": won},
        "scoreboard": scoreboard,
        "visionR": round(vision_px, 1),
        "buildings": [{"key": b.key, "x": P(b.x, b.y)[0], "y": P(b.x, b.y)[1],
                       "icon": b.icon, "kind": b.kind} for b in match.buildings],
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
           out_path: str) -> str:
    report = build_report(match, me, analyses)
    sprites = {b.icon: _b64(b.icon) for b in match.buildings}
    for ward_icon in ("ward_observer.png", "ward_sentry.png"):
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
  --bg:#0a0e1a; --bg-elev:#111827; --bg-soft:#1a2235;
  --line:#2a3548; --line-soft:#1c2536;
  --text:#f1f5f9; --muted:#94a3b8; --faint:#64748b;
  --you:#fbbf24; --radiant:#4ade80; --dire:#f87171;
  --ward:#e0b83a; --eward:#b56fd4; --accent:#fbbf24;
  --ok:#4ade80; --warn:#fbbf24; --bad:#f87171; --fight:#60a5fa;
  --font-display:"Outfit",system-ui,sans-serif;
  --font-body:"Source Sans 3",system-ui,sans-serif;
}
*{box-sizing:border-box}
html,body{height:100%;margin:0}
body{
  font-family:var(--font-body);
  background:
    radial-gradient(1100px 640px at 8% -8%, #152038 0%, transparent 55%),
    radial-gradient(900px 560px at 100% 0%, #1a1620 0%, transparent 48%),
    var(--bg);
  color:var(--text); overflow:hidden;
}
button{font:inherit;color:inherit;background:none;border:0;cursor:pointer;padding:0}
.app{display:flex;flex-direction:column;height:100vh;min-height:0}

.board{
  display:grid;grid-template-columns:1fr auto 1fr;align-items:center;gap:16px;
  padding:10px 18px;border-bottom:1px solid var(--line-soft);
  background:rgba(10,13,18,.8);backdrop-filter:blur(10px);flex:0 0 auto;
}
.team{display:flex;gap:8px;align-items:center}
.team.radiant{justify-content:flex-end}
.team.dire{justify-content:flex-start}
.slot{
  position:relative;width:52px;height:52px;border-radius:10px;overflow:hidden;
  background:#0d1219;border:2px solid transparent;opacity:.92;
}
.slot img{width:100%;height:100%;object-fit:cover;display:block}
.slot.radiant{border-color:var(--radiant)}
.slot.dire{border-color:var(--dire)}
.slot.me{box-shadow:0 0 0 2px var(--you);border-color:var(--you)}
.slot.dead{opacity:.28;filter:grayscale(.8)}
.slot.killer::after{
  content:"";position:absolute;inset:0;border:2px dashed rgba(239,91,91,.85);border-radius:6px;pointer-events:none;
}
.mid{text-align:center;min-width:180px}
.mid .brand{font-family:var(--font-display);font-weight:700;font-size:12px;letter-spacing:.12em;text-transform:uppercase;color:var(--muted)}
.mid .brand em{color:var(--accent);font-style:normal}
.mid .meta{font-size:12px;color:var(--faint);margin-top:2px}
.badge{display:inline-flex;font-size:10px;font-weight:700;letter-spacing:.06em;text-transform:uppercase;padding:3px 8px;border-radius:999px;border:1px solid transparent;margin-left:6px}
.badge.win{color:var(--ok);background:rgba(74,222,128,.12);border-color:rgba(74,222,128,.28)}
.badge.loss{color:var(--bad);background:rgba(248,113,113,.12);border-color:rgba(248,113,113,.28)}

.main{display:flex;flex:1;min-height:0}
.rail{width:280px;flex:0 0 auto;display:flex;flex-direction:column;min-height:0;border-right:1px solid var(--line-soft);background:rgba(17,24,39,.7)}
.rail-head{padding:16px 16px 12px;border-bottom:1px solid var(--line-soft)}
.rail-head h2{margin:0 0 12px;font-family:var(--font-display);font-size:10px;font-weight:700;letter-spacing:.15em;text-transform:uppercase;color:var(--accent)}
.sort{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.sort button{padding:8px 8px;border-radius:8px;font-size:12px;font-weight:600;color:var(--muted);background:var(--bg-soft);border:1px solid var(--line-soft)}
.sort button.on{color:var(--text);border-color:#3a4d66;background:#1a2330}
#deathlist{flex:1;overflow-y:auto;padding:12px}
.drow{display:grid;grid-template-columns:auto 1fr auto;gap:12px;align-items:start;padding:14px;border-radius:14px;cursor:pointer;margin-bottom:10px;border:1px solid transparent;border-left:3px solid transparent}
.drow:hover{background:rgba(255,255,255,.03)}
.drow.on{background:#182231;border-color:#334860;border-left-color:var(--accent)}
.drow .dot{width:10px;height:10px;border-radius:50%;margin-top:5px}
.drow .dot.critical{background:var(--bad)}
.drow .dot.notable{background:var(--warn)}
.drow .dot.fight{background:var(--fight)}
.drow .dot.normal{background:#4a586a}
.drow .clock{font-family:var(--font-display);font-weight:650;font-size:15px}
.drow .title{font-size:13px;color:#c5d0dc;margin-top:2px;line-height:1.3}
.drow .sev{font-size:11px;color:var(--faint);margin-top:4px;text-transform:uppercase;letter-spacing:.05em}
.drow .num{font-size:11px;color:var(--faint);font-weight:600;background:var(--bg);border:1px solid var(--line-soft);border-radius:6px;padding:2px 6px}

.mapwrap{flex:1;min-width:0;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:12px 16px;gap:10px}
.map-stage{position:relative;flex:1;min-height:0;aspect-ratio:1/1;max-width:100%;max-height:calc(100vh - 210px)}
svg{width:100%;height:100%;border-radius:16px;border:1px solid var(--line);background:#05070b}
.map-toggle{position:absolute;right:12px;top:12px;font-size:11px;font-weight:600;color:#d5deea;background:rgba(8,11,16,.78);border:1px solid rgba(255,255,255,.14);border-radius:999px;padding:6px 12px;cursor:pointer}
.map-toggle:hover{border-color:rgba(255,255,255,.32);color:#fff}

.transport{
  width:min(100%,640px);display:grid;grid-template-columns:auto 1fr auto;gap:12px;align-items:center;
  padding:10px 12px;border-radius:12px;background:rgba(16,21,29,.85);border:1px solid var(--line-soft);
}
.transport .play{
  width:40px;height:40px;border-radius:10px;border:1px solid var(--line);background:var(--bg-elev);
  font-size:16px;display:flex;align-items:center;justify-content:center;
}
.transport .play:hover{border-color:#3d516b}
.scrub{display:flex;flex-direction:column;gap:4px;min-width:0}
.scrub input[type=range]{width:100%;accent-color:var(--you)}
.scrub .times{display:flex;justify-content:space-between;font-size:11px;color:var(--faint);font-family:var(--font-display)}
.scrub .times strong{color:var(--text);font-weight:650}
.nav-death{display:flex;align-items:center;gap:8px;font-size:12px;color:var(--muted)}
.nav-death button{width:30px;height:30px;border-radius:8px;border:1px solid var(--line);background:var(--bg-elev);font-size:15px}
.nav-death button:disabled{opacity:.35;cursor:default}
.kbd{display:inline-block;font-size:10px;border:1px solid var(--line);border-radius:4px;padding:1px 5px;color:var(--muted)}

.panel{width:420px;flex:0 0 auto;overflow-y:auto;min-height:0;border-left:1px solid var(--line-soft);background:rgba(16,21,29,.78);padding:22px 22px 36px}
.sev-tag{display:inline-flex;font-size:11px;font-weight:700;letter-spacing:.07em;text-transform:uppercase;padding:5px 10px;border-radius:8px;border:1px solid transparent}
.sev-tag.critical{color:#ffb0b0;background:rgba(239,91,91,.12);border-color:rgba(239,91,91,.3)}
.sev-tag.notable{color:#ffd39a;background:rgba(224,162,58,.12);border-color:rgba(224,162,58,.3)}
.sev-tag.fight{color:#b7d3ff;background:rgba(110,168,255,.12);border-color:rgba(110,168,255,.3)}
.sev-tag.normal{color:#b7c2d0;background:rgba(255,255,255,.04);border-color:var(--line)}
.panel h1{font-family:var(--font-display);font-size:22px;font-weight:700;margin:14px 0 8px;line-height:1.2}
.panel .blurb{color:var(--muted);font-size:14.5px;line-height:1.5;margin:0 0 18px}
.lesson{
  margin:0 0 20px;padding:16px 16px;border-radius:14px;
  border:1px solid rgba(251,191,36,.35);background:rgba(251,191,36,.06);
}
.lesson .k{display:block;font-size:10px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;color:var(--accent);margin-bottom:8px}
.lesson .t{font-size:14px;font-weight:650;color:var(--text);line-height:1.45;margin:0 0 8px}
.lesson .s{font-size:12px;color:var(--muted);line-height:1.45;margin:0}
.story,.situation{margin:0 0 18px;padding:14px 16px;border-radius:12px;font-size:13.5px;line-height:1.5}
.story{background:rgba(96,165,250,.08);border:1px solid rgba(96,165,250,.22);color:#cfe0ff;border-left:3px solid var(--fight)}
.situation{background:var(--bg-soft);border:1px solid var(--line-soft);color:#c5d0dc}
.story .k{display:block;font-size:10px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;margin-bottom:6px;color:var(--fight)}
.section{margin:0 0 24px;padding-bottom:4px}
.section h3{margin:0 0 12px;font-family:var(--font-display);font-size:10px;font-weight:700;letter-spacing:.15em;text-transform:uppercase;color:var(--accent)}
.chips{display:flex;flex-wrap:wrap;gap:8px}
.chip{font-size:12.5px;font-weight:600;color:#d5deea;background:var(--bg-soft);border:1px solid var(--line);border-radius:999px;padding:6px 11px}
.kb{display:flex;align-items:center;gap:10px;font-size:14px;color:#c5d0dc;padding:12px 14px;border-radius:12px;background:var(--bg-soft);border:1px solid var(--line-soft)}
.kb img{width:28px;height:28px;border-radius:50%;border:1.5px solid var(--dire)}
.meters{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.meter{padding:12px 14px;border-radius:12px;background:var(--bg-soft);border:1px solid var(--line-soft)}
.meter .lbl{font-size:11px;color:var(--faint);text-transform:uppercase;letter-spacing:.06em}
.meter .val{font-family:var(--font-display);font-weight:650;font-size:15px;margin-top:2px}
.bar{height:6px;border-radius:3px;background:#232b36;margin-top:8px;overflow:hidden}
.bar i{display:block;height:100%;border-radius:3px}
.lvl{display:flex;align-items:center;justify-content:center;font-family:var(--font-display);font-weight:700;font-size:18px;border-radius:12px;background:var(--bg-soft);border:1px solid var(--line-soft)}
.findings{display:flex;flex-direction:column;gap:10px}
.finding{display:grid;grid-template-columns:1fr;gap:5px;padding:14px 14px;border-radius:12px;background:var(--bg-elev);border:1px solid var(--line-soft);border-left:3px solid var(--line)}
.finding.bad{border-left-color:var(--bad)}
.finding.warn{border-left-color:var(--warn)}
.finding.ok{border-left-color:var(--ok)}
.finding .ft{font-weight:650;font-size:13px}
.finding .fx{font-size:13px;color:var(--muted);line-height:1.45}
.statuses{display:flex;flex-wrap:wrap;gap:8px}
.stchip{
  display:inline-flex;align-items:center;gap:6px;font-size:11.5px;font-weight:700;
  letter-spacing:.04em;text-transform:uppercase;padding:6px 10px;border-radius:999px;
  border:1px solid currentColor;background:rgba(0,0,0,.25);
}
.stchip.good{background:rgba(74,222,128,.08)}
.stchip.bad{background:rgba(248,113,113,.06)}
.stchip .src{font-weight:500;text-transform:none;letter-spacing:0;color:var(--muted);font-size:11px}
.next{
  margin:8px 0 20px;padding:16px 16px;border-radius:14px;
  border:1px solid var(--line);background:var(--bg-elev);
}
.next .k{display:block;font-size:10px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;color:var(--accent);margin-bottom:10px}
.next ol{margin:0;padding:0;list-style:none;display:flex;flex-direction:column;gap:10px}
.next li{display:flex;gap:10px;align-items:flex-start;font-size:13.5px;line-height:1.4;color:#d5deea}
.next .n{
  flex:0 0 auto;width:24px;height:24px;border-radius:50%;
  display:flex;align-items:center;justify-content:center;
  font-family:var(--font-display);font-size:12px;font-weight:700;
  color:var(--accent);background:rgba(251,191,36,.12);border:1px solid rgba(251,191,36,.28);
}
.tchip{
  display:inline-flex;align-items:center;gap:5px;font-size:11.5px;font-weight:600;
  color:#d5deea;background:var(--bg-elev);border:1px solid var(--line);border-radius:999px;padding:3px 8px 3px 3px;
}
.tchip img{width:18px;height:18px;border-radius:4px;background:#0d1219}
.tchip .tm{
  font-family:var(--font-display);font-size:10px;font-weight:700;letter-spacing:.04em;
  color:var(--accent);background:rgba(251,191,36,.12);border:1px solid rgba(251,191,36,.25);
  border-radius:999px;padding:2px 6px;
}
.tchip.ult{border-color:rgba(251,191,36,.45);background:rgba(251,191,36,.07)}
.tchip .who{color:var(--faint);font-weight:500;font-size:10.5px}
.roster{display:flex;flex-direction:column;gap:8px}
.rrow{display:grid;grid-template-columns:30px 1fr auto;gap:12px;align-items:center;padding:10px 12px;border-radius:12px;background:var(--bg-soft);border:1px solid var(--line-soft)}
.rrow img{width:30px;height:30px;border-radius:50%;border:2px solid #445}
.rrow.radiant img{border-color:var(--radiant)}
.rrow.dire img{border-color:var(--dire)}
.rrow.me img{border-color:var(--you)}
.rrow .rn{font-weight:650;font-size:13px}
.rrow .rm{font-size:11.5px;color:var(--faint);margin-top:1px}
.rrow .rd{font-family:var(--font-display);font-size:12px;font-weight:650;color:var(--muted);text-align:right}
.rrow .tagkill{display:inline-block;margin-left:6px;font-size:10px;font-weight:700;letter-spacing:.04em;text-transform:uppercase;color:#ffb0b0}
.eload{display:flex;flex-direction:column;gap:10px}
.erow{padding:12px 14px;border-radius:12px;background:var(--bg-soft);border:1px solid var(--line-soft)}
.erow .etop{display:flex;align-items:center;gap:8px;margin-bottom:8px}
.erow img.hero{width:28px;height:28px;border-radius:50%;border:2px solid var(--dire)}
.erow.ally img.hero{border-color:var(--radiant)}
.erow .ename{font-weight:650;font-size:13px}
.erow .eflags{font-size:11px;color:var(--warn);margin-left:auto}
.erow .eitems,.items{display:flex;flex-wrap:wrap;gap:5px;align-items:center}
.erow .eitems img,.items img{
  width:42px;height:32px;border-radius:5px;background:#0d1219;border:1px solid var(--line);
  object-fit:cover;display:block;
}
.items img.neu{outline:1px solid var(--accent);outline-offset:1px}
.erow .eitems .ph,.items .ph{
  width:42px;height:32px;border-radius:5px;background:#0d1219;border:1px solid var(--line);
  font-size:8px;color:var(--faint);display:flex;align-items:center;justify-content:center;text-align:center;padding:2px;
}
.ults{display:flex;flex-direction:column;gap:8px}
.ult{
  display:grid;grid-template-columns:28px 1fr auto;gap:10px;align-items:center;
  padding:10px 12px;border-radius:12px;background:var(--bg-soft);border:1px solid var(--line-soft);
}
.ult img{width:28px;height:28px;border-radius:6px;background:#0d1219}
.ult .un{font-weight:650;font-size:13px}
.ult .ux{font-size:12px;color:#aeb9c7;margin-top:2px;line-height:1.35}
.ult .ustate{font-size:11px;font-weight:700;letter-spacing:.04em;text-transform:uppercase}
.ult .ustate.ready{color:var(--ok)}
.ult .ustate.cooldown{color:var(--warn)}
.ult .ustate.unskilled,.ult .ustate.unknown{color:var(--faint)}
.casts{display:flex;flex-wrap:wrap;gap:5px}
.crow{
  display:none;
}
details.sec{margin:0 0 14px;border:1px solid var(--line-soft);border-radius:12px;background:rgba(255,255,255,.015)}
details.sec summary{cursor:pointer;padding:12px 14px;font-family:var(--font-display);font-size:10px;font-weight:700;letter-spacing:.14em;text-transform:uppercase;color:var(--muted);list-style:none;display:flex;align-items:center;justify-content:space-between;gap:8px}
details.sec summary::-webkit-details-marker{display:none}
details.sec summary::after{content:"+";color:var(--faint);font-size:14px;font-weight:600}
details.sec[open] summary::after{content:"\2212"}
details.sec summary:hover{color:var(--text)}
details.sec .inner{padding:2px 14px 14px}
.subh{font-size:10px;font-weight:700;letter-spacing:.12em;text-transform:uppercase;color:var(--faint);margin:14px 0 8px}
.subh:first-child{margin-top:0}
.leg{font-size:12px;color:var(--faint);line-height:1.7;border-top:1px solid var(--line-soft);padding-top:12px}
.sw{display:inline-block;width:9px;height:9px;border-radius:50%;margin:0 4px 0 8px;vertical-align:0}
.sw:first-child{margin-left:0}
.hint{font-size:11.5px;color:var(--faint);margin-top:6px}

@media (max-width:980px){
  .panel{width:360px}.rail{width:240px}
  .board{grid-template-columns:1fr;justify-items:center}
  .team{justify-content:center !important}
}
@media (max-width:860px){
  body{overflow:auto}.app{height:auto;min-height:100vh}
  .main{flex-direction:column}
  .rail,.panel{width:100%;border:0}
  .rail{max-height:200px;border-bottom:1px solid var(--line-soft)}
  .map-stage{max-height:none;width:min(92vw,640px);height:auto}
}
</style>
</head><body>
<div class="app">
  <header class="board">
    <div class="team radiant" id="sb-radiant"></div>
    <div class="mid">
      <div class="brand">Replay <em>Coach</em></div>
      <div class="meta" id="h-sub"></div>
    </div>
    <div class="team dire" id="sb-dire"></div>
  </header>

  <div class="main">
    <aside class="rail">
      <div class="rail-head">
        <h2>Your deaths</h2>
        <div class="sort">
          <button type="button" id="sort-priority" class="on">Most important</button>
          <button type="button" id="sort-time">By time</button>
        </div>
      </div>
      <div id="deathlist"></div>
    </aside>

    <section class="mapwrap">
      <div class="map-stage">
        <svg id="map" viewBox="0 0 640 640" role="img" aria-label="Minimap playback">
          <image id="map-bg" href="__MAP2_B64__" x="0" y="0" width="640" height="640"></image>
          <g id="ov"></g>
        </svg>
        <button type="button" class="map-toggle" id="map-toggle">Map: In-game</button>
      </div>
      <div class="transport">
        <button type="button" class="play" id="btn-play" aria-label="Play or pause">▶</button>
        <div class="scrub">
          <input type="range" id="scrub" min="0" max="20" step="1" value="20">
          <div class="times"><span id="t-rel">-10.0s</span><strong id="t-clock">0:00</strong><span>death</span></div>
        </div>
        <div class="nav-death">
          <button type="button" id="prev" aria-label="Previous death">‹</button>
          <span id="pos">1/1</span>
          <button type="button" id="next" aria-label="Next death">›</button>
          <span class="kbd">Space</span>
        </div>
      </div>
    </section>

    <aside class="panel" id="panel" aria-live="polite"></aside>
  </div>
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

// Jump classification (640px map ≈ 4.32px per grid; blink ~1200u ≈ 40px).
// Mid-range jumps are Blink (dagger / blink abilities). Only long jumps are TP.
const BLINK_MIN_PX = 22;   // above force-staff-ish hops
const BLINK_MAX_PX = 58;   // blink range + margin
const TP_MIN_PX = 70;      // clearly beyond blink → town portal / BoTs

function classifyJump(from, to){
  const dist = Math.hypot(to.x - from.x, to.y - from.y);
  if (dist >= TP_MIN_PX) return {kind: "tp", dist};
  if (dist >= BLINK_MIN_PX && dist <= BLINK_MAX_PX) return {kind: "blink", dist};
  if (dist >= 14 && dist < BLINK_MIN_PX && to.hasForce) return {kind: "force", dist};
  return null;
}

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
    cx:m.x, cy:m.y, r:r, fill:"none", stroke:"#070a0e", "stroke-width": 2.4
  }));
  g.appendChild(el("circle", {
    cx:m.x, cy:m.y, r:r, fill:"none", stroke:col, "stroke-width": 2
  }));
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
    + (m.hasBlink ? " · Blink" : "")
    + (m.jumpKind === "tp" ? " · teleporting" : "")
    + (m.jumpKind === "blink" ? " · blinking" : "");
  g.appendChild(title);
  return g;
}

function drawJump(from, to, kind){
  const col = kind === "tp" ? "#c9a227" : (kind === "force" ? "#7ec8ff" : "#d7e6ff");
  const label = kind === "tp" ? "TP" : (kind === "force" ? "Force" : "Blink");
  ov.appendChild(el("line", {
    x1: from.x, y1: from.y, x2: to.x, y2: to.y,
    stroke: col, "stroke-width": kind === "tp" ? 1.8 : 1.4,
    "stroke-dasharray": kind === "tp" ? "6 4" : "3 3",
    opacity: .92, "stroke-linecap": "round"
  }));
  ov.appendChild(el("circle", {cx: from.x, cy: from.y, r: 2.0, fill: col, opacity: .75}));
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
    if (d > BLINK_MIN_PX){ if (cur.length>1) segs.push(cur); cur = [pts[i]]; }
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
    const jump = p ? classifyJump(p, m) : null;
    m.jumpKind = jump ? jump.kind : null;
  }
  const meM = marks.find(m => m.me) || marks[0];
  ov.innerHTML = "";

  const dead = new Set(F.deadBuildings);
  const sz = {tower:16, rax:13, fort:28};
  for (const b of R.buildings){
    if (dead.has(b.key)) continue;
    const s = sz[b.kind] || 14;
    ov.appendChild(el("image", {href:SPR[b.icon], x:b.x-s/2, y:b.y-s/2, width:s, height:s}));
  }
  for (const w of F.wards){
    const kind = w.kind || "observer";
    const icon = kind === "sentry" ? "ward_sentry.png" : "ward_observer.png";
    const href = SPR[icon];
    // Glyphs are 35x27 — keep that aspect so the eye reads as an eye.
    const gw = kind === "sentry" ? 16 : 18;
    const gh = Math.round(gw * 27 / 35);
    if (href){
      ov.appendChild(el("image", {
        href, x: w.x - gw/2, y: w.y - gh/2, width: gw, height: gh,
        opacity: w.isRadiant ? 0.95 : 0.88
      }));
      // Tiny team tint under the icon so Radiant/Dire stay clear.
      ov.appendChild(el("circle", {
        cx: w.x, cy: w.y + gh/2 + 1, r: 2.2,
        fill: w.isRadiant ? "#4ade80" : "#f87171",
        stroke: "#070a0e", "stroke-width": 0.8
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
    if (!m.jumpKind) continue;
    const p = prevBy[m.heroId];
    if (p) drawJump(p, m, m.jumpKind);
  }

  // Ultimate casts near this frame — gold burst + ability icon at caster.
  const ULT_SHOW = 1.6;
  for (const u of (pb.ultCasts || [])){
    const age = F.t - u.t;
    if (age < -0.05 || age > ULT_SHOW) continue;
    const fade = 1 - age / ULT_SHOW;
    const col = u.me ? "#fbbf24" : (u.isRadiant ? "#4ade80" : "#f87171");
    // Prefer live hero position if they're on this frame.
    const live = marks.find(m => m.heroId === u.heroId);
    const cx = live ? live.x : u.x;
    const cy = live ? live.y : u.y;
    ov.appendChild(el("circle", {
      cx, cy, r: 18 + fade * 10, fill: "none", stroke: col,
      "stroke-width": 2.2, opacity: 0.25 + fade * 0.55
    }));
    ov.appendChild(el("circle", {
      cx, cy, r: 12, fill: col, opacity: 0.12 + fade * 0.18
    }));
    if (u.icon){
      const s = 18;
      ov.appendChild(el("image", {
        href: u.icon, x: cx - s/2, y: cy - 34, width: s, height: s,
        opacity: 0.55 + fade * 0.45
      }));
    }
    const lab = el("text", {
      x: cx, y: cy - 38, fill: "#ffe6a0", "font-size": 8, "font-weight": 700,
      "font-family": "Outfit, sans-serif", "text-anchor": "middle",
      stroke: "#070a0e", "stroke-width": 2.5, "paint-order": "stroke",
      opacity: 0.65 + fade * 0.35
    });
    lab.textContent = "ULT";
    ov.appendChild(lab);
  }

  // Item uses — smaller cyan/amber icon ping near the user.
  const ITEM_SHOW = 1.4;
  for (const it of (pb.itemCasts || [])){
    const age = F.t - it.t;
    if (age < -0.05 || age > ITEM_SHOW) continue;
    const fade = 1 - age / ITEM_SHOW;
    const live = marks.find(m => m.heroId === it.heroId);
    const cx = live ? live.x : it.x;
    const cy = live ? live.y : it.y;
    const col = "#67e8f9";
    ov.appendChild(el("circle", {
      cx, cy, r: 15 + fade * 6, fill: "none", stroke: col,
      "stroke-width": 1.6, opacity: 0.2 + fade * 0.45,
      "stroke-dasharray": "3 2"
    }));
    if (it.icon){
      const s = 14;
      ov.appendChild(el("image", {
        href: it.icon, x: cx + 10, y: cy - 18, width: s, height: s,
        opacity: 0.55 + fade * 0.45
      }));
    }
  }

  for (const m of marks) if (!m.me) ov.appendChild(marker(m));
  if (meM) ov.appendChild(marker(meM));

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
    const div = document.createElement("div");
    div.className = "drow" + (i === cur ? " on" : "");
    div.innerHTML =
      `<span class="dot ${esc(d.severity.key)}"></span>
       <div><div class="clock">${esc(d.clock)}</div><div class="title">${esc(d.title)}</div>
       <div class="sev">${esc(d.severity.label)}</div></div>
       <span class="num">#${d.idx+1}</span>`;
    div.onclick = () => show(i);
    nav.appendChild(div);
  });
}

function paintBoard(){
  const paint = (elId, rows) => {
    const root = document.getElementById(elId);
    root.innerHTML = rows.map(h => `
      <div class="slot ${h.isRadiant?"radiant":"dire"}${h.me?" me":""}" data-hero-id="${h.heroId}" title="${esc(h.name)}">
        ${h.icon ? `<img src="${esc(h.icon)}" alt="${esc(h.name)}">` : ""}
      </div>`).join("");
  };
  paint("sb-radiant", R.scoreboard.radiant);
  paint("sb-dire", R.scoreboard.dire);
  const H = R.hero, S = R.summary;
  const result = H.won == null ? "" : H.won
    ? `<span class="badge win">Victory</span>` : `<span class="badge loss">Defeat</span>`;
  document.getElementById("h-sub").innerHTML =
    `${esc(H.name)} · ${esc(H.side)} · match ${R.matchId} · ${S.deathCount} deaths ${result}`;
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
    <details class="sec" open><summary>Who was here · ${counts.allies||0} allies · ${counts.enemies||0} enemies</summary>
      <div class="inner"><div class="roster">${roster}</div></div></details>
    <details class="sec"><summary>Items at that moment</summary><div class="inner">
      <div class="subh">Your items</div><div class="items">${inv||`<span class="chip">No item data</span>`}</div>
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
// Each map image needs its own placement so the playable area fills the 0..640
// marker frame. Dark map fills edge-to-edge; the in-game map has a border, so
// it's scaled up (overhang is clipped by the SVG viewBox). Derived by
// registering the two images: playable scale 0.8969, insets L 0.0531 / T 0.0406.
const MB = 640;
const IN_SCALE = 0.8969, IN_L = 0.0531, IN_T = 0.0406;
const IN_W = MB / IN_SCALE;
const MAP_CFG = {
  ingame: {href:"__MAP2_B64__", x:-IN_L*IN_W, y:-IN_T*IN_W, w:IN_W, h:IN_W, label:"Map: In-game"},
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
