"""Open-source meta data: what actually works on this hero, in this role.

Two free sources, no scraping:
  * STRATZ  — item purchases for the hero filtered to the player's POSITION,
              with win rate and average purchase minute.
  * OpenDota — hero-vs-hero matchup win rates, so "this draft was bad for them"
              is a measured claim rather than a vibe.

Dotabuff is deliberately not used: it has no public API, and scraping it would
breach their terms and break on any layout change.

Everything is disk-cached and degrades to {} — the report works without it.
"""
from __future__ import annotations
import json
import os
import time

import requests

import items as items_mod

_DIR = os.path.join(os.path.dirname(__file__), "meta_cache")
_TTL = 7 * 24 * 3600            # meta shifts slowly; a week is plenty
_STRATZ = "https://api.stratz.com/graphql"
_POSITION = {1: "POSITION_1", 2: "POSITION_2", 3: "POSITION_3",
             4: "POSITION_4", 5: "POSITION_5"}
MIN_SAMPLE = 400                # ignore items with too little data to mean anything


def _cache(name: str):
    os.makedirs(_DIR, exist_ok=True)
    p = os.path.join(_DIR, name)
    if os.path.exists(p) and time.time() - os.path.getmtime(p) < _TTL:
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            pass
    return None


def _store(name: str, data):
    try:
        json.dump(data, open(os.path.join(_DIR, name), "w", encoding="utf-8"))
    except Exception:
        pass
    return data


def hero_items(hero_id: int, lane_role: int | None = None) -> list[dict]:
    """Item win rates + average purchase minute for this hero (and role)."""
    pos = _POSITION.get(lane_role or 0)
    key = f"items_{hero_id}_{pos or 'all'}.json"
    hit = _cache(key)
    if hit is not None:
        return hit

    token = os.environ.get("STRATZ_TOKEN")
    if not token:
        return []
    arg = f"heroId: {int(hero_id)}" + (f", positionIds: {pos}" if pos else "")
    query = ("{ heroStats { itemFullPurchase(%s) "
             "{ itemId matchCount winCount time } } }" % arg)
    try:
        r = requests.post(_STRATZ, json={"query": query}, timeout=40,
                          headers={"Authorization": f"Bearer {token}",
                                   "Content-Type": "application/json",
                                   "User-Agent": "DotaReplayCoach/0.1 (personal)"})
        rows = r.json()["data"]["heroStats"]["itemFullPurchase"] or []
    except Exception:
        return []

    db = items_mod.load()
    agg: dict[int, dict] = {}
    for x in rows:
        a = agg.setdefault(x["itemId"], {"m": 0, "w": 0, "t": 0.0})
        a["m"] += x["matchCount"]
        a["w"] += x["winCount"]
        a["t"] += (x["time"] or 0) * x["matchCount"]      # `time` is in minutes
    out = []
    for iid, v in agg.items():
        if v["m"] < MIN_SAMPLE:
            continue
        avg_min = v["t"] / v["m"]
        out.append({
            "item": (db.get(str(iid)) or {}).get("name", str(iid)),
            "matches": v["m"],
            "win_rate": round(100 * v["w"] / v["m"], 1),
            "typical_purchase": f"{int(avg_min)}:{int(round((avg_min % 1) * 60)):02d}",
        })
    out.sort(key=lambda r: -r["matches"])
    return _store(key, out[:14])


def item_popularity(hero_id: int) -> dict:
    """What this hero actually builds, by game phase (OpenDota).

    Needed because STRATZ's itemFullPurchase only covers the early core — it
    returns ~5 items and omits BKB, Manta, Butterfly entirely, which would make
    any "is this item normal on this hero" judgement badly wrong.
    """
    key = f"popularity_{hero_id}.json"
    hit = _cache(key)
    if hit is not None:
        return hit
    try:
        raw = requests.get(
            f"https://api.opendota.com/api/heroes/{int(hero_id)}/itemPopularity",
            timeout=30).json()
    except Exception:
        return {}
    db = items_mod.load()
    out = {}
    for phase in ("early_game_items", "mid_game_items", "late_game_items"):
        rows = sorted((raw.get(phase) or {}).items(), key=lambda kv: -kv[1])[:10]
        out[phase.replace("_items", "")] = [
            (db.get(i) or {}).get("name", i) for i, _ in rows]
    return _store(key, out)


def matchups(hero_id: int, vs_hero_ids: list[int], hname) -> list[dict]:
    """This hero's measured win rate against each enemy hero."""
    key = f"matchups_{hero_id}.json"
    rows = _cache(key)
    if rows is None:
        try:
            r = requests.get(
                f"https://api.opendota.com/api/heroes/{int(hero_id)}/matchups",
                timeout=30)
            rows = _store(key, r.json())
        except Exception:
            return []
    by_id = {x["hero_id"]: x for x in rows if x.get("games_played")}
    out = []
    for hid in vs_hero_ids:
        x = by_id.get(hid)
        if not x or x["games_played"] < 10:
            continue
        # Sample size travels with the number. These are often only 15-50 games,
        # which is far too few to call a matchup — the consumer must be told.
        out.append({"vs": hname(hid),
                    "win_rate": round(100 * x["wins"] / x["games_played"], 1),
                    "games": x["games_played"],
                    "reliable": x["games_played"] >= 50})
    out.sort(key=lambda r: -r["win_rate"])
    return out
