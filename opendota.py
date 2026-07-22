"""OpenDota backfill: observer wards, building-kill timeline, building positions.

Ward coords from obs_log are already in the same 64..192 grid space as STRATZ
position events (verified: x 90..190 on a real match). Building positions come
from assets/building_data.json (MangoByte), also in grid space, keyed by the
same npc_dota_* names OpenDota's objectives use.

When a match has no replay parse yet (`version` is null), we POST
`/api/request/{match_id}` so OpenDota parses it — that unlocks purchase_log,
wards, lh_t, and the rest of the full picture.
"""
from __future__ import annotations
import json
import os
import time
import requests
from models import Ward, Building

_ASSETS = os.path.join(os.path.dirname(__file__), "assets")
_CACHE_DIR = os.path.join(os.path.dirname(__file__), "od_cache")
_API = "https://api.opendota.com/api"

OBS_LIFETIME = 360.0   # observer ward duration in seconds
SEN_LIFETIME = 420.0   # sentry ward duration in seconds


def cache_path(match_id: int) -> str:
    return os.path.join(_CACHE_DIR, f"{match_id}.json")


def is_fully_parsed(od: dict | None) -> bool:
    """True when OpenDota has finished a replay parse (not just Steam GC summary)."""
    if not od:
        return False
    # `version` is set only after the .dem is parsed.
    if od.get("version") is None:
        return False
    players = od.get("players") or []
    if not players:
        return False
    with_log = sum(1 for p in players if p.get("purchase_log"))
    return with_log >= max(1, len(players) // 2)


def _get_match(match_id: int) -> dict:
    r = requests.get(f"{_API}/matches/{match_id}", timeout=60)
    r.raise_for_status()
    return r.json()


def request_parse(match_id: int) -> int | None:
    """Ask OpenDota to parse the replay. Returns jobId when accepted."""
    r = requests.post(f"{_API}/request/{match_id}", timeout=30)
    r.raise_for_status()
    body = r.json() if r.content else {}
    job = (body or {}).get("job") or body or {}
    jid = job.get("jobId") or job.get("job_id")
    return int(jid) if jid is not None else None


def job_status(job_id: int):
    """Poll parse job. OpenDota returns null when the job is gone/finished."""
    r = requests.get(f"{_API}/request/{job_id}", timeout=30)
    r.raise_for_status()
    if not r.content or r.text.strip() in ("", "null"):
        return None
    try:
        return r.json()
    except Exception:
        return None


def ensure_parsed(match_id: int, wait: bool = True,
                  poll_s: float = 15.0, timeout_s: float = 600.0,
                  force: bool = False) -> dict:
    """Fetch match; if unparsed (or force), submit a parse request and wait."""
    od = _get_match(match_id)
    if is_fully_parsed(od) and not force:
        return od

    reason = "forced re-parse" if force else "not fully parsed yet (no replay parse)"
    print(f"  OpenDota: {reason} — requesting parse...")
    try:
        jid = request_parse(match_id)
        if jid is not None:
            print(f"  OpenDota parse job {jid} submitted")
        else:
            print("  OpenDota parse requested (no job id returned)")
    except Exception as e:
        print(f"  OpenDota parse request failed: {e}")
        return od

    if not wait:
        return od

    deadline = time.time() + timeout_s
    while time.time() < deadline:
        time.sleep(poll_s)
        try:
            od = _get_match(match_id)
        except Exception as e:
            print(f"  OpenDota poll error: {e}")
            continue
        if is_fully_parsed(od):
            print("  OpenDota parse ready")
            return od
        print(f"  waiting for OpenDota parse... "
              f"({int(deadline - time.time())}s left)")
    print("  OpenDota parse still pending — continuing with whatever we have")
    return od


def fetch(match_id: int, *, ensure: bool = True, wait_parse: bool = True,
          force_parse: bool = False, use_cache: bool = True,
          parse_timeout: float = 600.0) -> dict:
    """GET the OpenDota match, optionally requesting a full replay parse first."""
    os.makedirs(_CACHE_DIR, exist_ok=True)
    cache = cache_path(match_id)

    if use_cache and os.path.exists(cache) and not force_parse:
        try:
            cached = json.load(open(cache, encoding="utf-8"))
            if is_fully_parsed(cached):
                return cached
            # Stale unparsed cache — fall through and refresh / request parse.
            print("  OpenDota cache is incomplete — refreshing")
        except Exception:
            pass

    if ensure:
        od = ensure_parsed(match_id, wait=wait_parse, force=force_parse,
                           timeout_s=parse_timeout)
    else:
        od = _get_match(match_id)

    json.dump(od, open(cache, "w", encoding="utf-8"))
    return od


def _ward_log(od: dict, place_key: str, left_key: str, kind: str,
              lifetime: float) -> list[Ward]:
    out = []
    for p in od.get("players") or []:
        is_rad = (p.get("player_slot") or 0) < 128
        left_by_handle = {}
        for ev in p.get(left_key) or []:
            if ev.get("ehandle") is not None:
                left_by_handle[ev["ehandle"]] = ev.get("time")
        for ev in p.get(place_key) or []:
            t0 = ev.get("time")
            if t0 is None or ev.get("x") is None:
                continue
            t1 = left_by_handle.get(ev.get("ehandle"))
            out.append(Ward(
                x=ev["x"], y=ev["y"], time_from=t0,
                time_to=t1 if t1 is not None else t0 + lifetime,
                is_radiant=is_rad, kind=kind,
            ))
    return out


def wards(od: dict) -> list[Ward]:
    """Observer + sentry wards from every player (god view), with lifetimes."""
    return (
        _ward_log(od, "obs_log", "obs_left_log", "observer", OBS_LIFETIME)
        + _ward_log(od, "sen_log", "sen_left_log", "sentry", SEN_LIFETIME)
    )


def building_kills(od: dict) -> list[tuple[float, str]]:
    return [(o["time"], o["key"]) for o in (od.get("objectives") or [])
            if o.get("type") == "building_kill" and o.get("key")]


def load_buildings() -> list[Building]:
    path = os.path.join(_ASSETS, "building_data.json")
    rows = json.load(open(path, encoding="utf-8"))
    out = []
    for b in rows:
        key = b["key"]
        out.append(Building(
            key=key, x=b["x"], y=b["y"], icon=b["icon"],
            is_radiant="goodguys" in key,
            kind=("fort" if "fort" in key else "rax" if "rax" in key else "tower"),
        ))
    return out


def attach_economy(match, od: dict) -> None:
    """Copy OpenDota economy + purchase log onto STRATZ hero tracks.

    STRATZ inventoryEvents are often sparse/empty; OpenDota purchase_log is the
    reliable source for 'did they have Blink at this minute?'.
    """
    od_players = od.get("players") or []
    used: set[int] = set()
    for track in match.players:
        cand = None
        for i, p in enumerate(od_players):
            if i in used:
                continue
            if p.get("hero_id") != track.hero_id:
                continue
            is_rad = bool(p.get("isRadiant")) if "isRadiant" in p else (p.get("player_slot") or 0) < 128
            if is_rad != track.is_radiant:
                continue
            cand = (i, p)
            break
        if not cand:
            continue
        i, p = cand
        used.add(i)
        track.econ_times = list(p.get("times") or [])
        track.lh_t = list(p.get("lh_t") or [])
        track.gold_t = list(p.get("gold_t") or [])
        track.purchase_log = [
            {"time": ev.get("time"), "key": ev.get("key")}
            for ev in (p.get("purchase_log") or [])
            if ev.get("key") is not None and ev.get("time") is not None
        ]
        track.final_items = [p.get(f"item_{s}") or 0 for s in range(6)]
        track.final_neutral = p.get("item_neutral") or None
