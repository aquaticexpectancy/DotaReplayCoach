"""STRATZ GraphQL client: fetch a match and wait for it to be parsed.

NOTE ON FIELD NAMES: the query below reflects the confirmed shape
(`playbackData.playerUpdatePositionEvents { time x y }`) but some sibling
fields (death coord keys, ward container) vary by schema version. The parser
is defensive (`_first_key`) so a rename degrades gracefully instead of crashing.
Verify against a live response in milestone M1 and tighten the query.
"""
from __future__ import annotations
import os
import time
import requests
from models import MatchData, HeroTrack, Position, DeathEvent
from positions import pos_at

ENDPOINT = "https://api.stratz.com/graphql"

_QUERY = """
query ($id: Long!) {
  match(id: $id) {
    parsedDateTime
    didRadiantWin
    players {
      heroId
      isRadiant
      steamAccountId
      playbackData {
        playerUpdatePositionEvents { time x y }
        playerUpdateHealthEvents { time hp maxHp mp maxMp }
        playerUpdateLevelEvents { time level }
        inventoryEvents {
          time
          item0 { itemId } item1 { itemId } item2 { itemId }
          item3 { itemId } item4 { itemId } item5 { itemId }
          neutral0 { itemId }
        }
        abilityUsedEvents { time abilityId target attacker }
        itemUsedEvents { time itemId target attacker }
        deathEvents {
          time positionX positionY
          goldLost goldFed timeDead
          attacker assist
          isDieBack isBurst isEngagedOnDeath isAttemptTpOut
          hasHealAvailable isFeed isWardWalkThrough
        }
      }
    }
  }
}
"""


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        # STRATZ asks callers to identify themselves:
        "User-Agent": "DotaReplayCoach/0.1 (personal)",
    }


def _first_key(d: dict, *keys, default=None):
    for k in keys:
        if isinstance(d, dict) and d.get(k) is not None:
            return d[k]
    return default


def _post(token: str, variables: dict) -> dict:
    r = requests.post(ENDPOINT, json={"query": _QUERY, "variables": variables},
                      headers=_headers(token), timeout=60)
    if r.status_code >= 400:
        # STRATZ puts the real reason (bad field name, etc.) in the body.
        raise RuntimeError(f"STRATZ HTTP {r.status_code}:\n{r.text}")
    body = r.json()
    if body.get("errors"):
        raise RuntimeError(f"STRATZ GraphQL errors: {body['errors']}")
    return body["data"]["match"]


_DOWNLOAD_MUTATION = "mutation ($id: Long!) { retryMatchDownload(matchId: $id) }"


def request_download(match_id: int, token: str | None = None) -> bool:
    """Ask STRATZ to download + parse this match now. Returns True if accepted.
    This is the nudge that turns 'waiting for STRATZ' into 'STRATZ parsing now'."""
    token = token or os.environ.get("STRATZ_TOKEN")
    try:
        r = requests.post(ENDPOINT,
                          json={"query": _DOWNLOAD_MUTATION, "variables": {"id": match_id}},
                          headers=_headers(token), timeout=30)
        if r.status_code >= 400:
            return False
        return bool((r.json().get("data") or {}).get("retryMatchDownload"))
    except Exception:
        return False


_FLAG_FIELDS = ("isDieBack", "isBurst", "isEngagedOnDeath", "isAttemptTpOut",
                "hasHealAvailable", "isFeed", "isWardWalkThrough")


def _parse_track(pdata: dict, hero_id: int, is_radiant: bool, acct: int | None) -> HeroTrack:
    positions = [Position(ev["time"], ev["x"], ev["y"])
                 for ev in (pdata.get("playerUpdatePositionEvents") or [])]
    positions.sort(key=lambda p: p.time)
    track = HeroTrack(hero_id=hero_id, is_radiant=is_radiant,
                      account_id=acct, positions=positions, deaths=[])
    track.health = sorted(
        (ev["time"], ev.get("hp"), ev.get("maxHp"), ev.get("mp"), ev.get("maxMp"))
        for ev in (pdata.get("playerUpdateHealthEvents") or []))
    track.levels = sorted(
        (ev["time"], ev.get("level") or 1)
        for ev in (pdata.get("playerUpdateLevelEvents") or []))
    inv = []
    for ev in (pdata.get("inventoryEvents") or []):
        slots = [(ev.get(f"item{i}") or {}).get("itemId") for i in range(6)]
        inv.append((ev["time"],
                    [s for s in slots if s],
                    (ev.get("neutral0") or {}).get("itemId")))
    track.inventory = sorted(inv, key=lambda r: r[0])
    track.ability_casts = sorted(
        (ev["time"], ev.get("abilityId"), ev.get("target"))
        for ev in (pdata.get("abilityUsedEvents") or [])
        if ev.get("time") is not None and ev.get("abilityId") is not None
    )
    track.item_casts = sorted(
        (ev["time"], ev.get("itemId"), ev.get("target"))
        for ev in (pdata.get("itemUsedEvents") or [])
        if ev.get("time") is not None and ev.get("itemId") is not None
    )
    for ev in (pdata.get("deathEvents") or []):
        x, y = ev.get("positionX"), ev.get("positionY")
        if x is None or y is None:            # fall back to the position track
            p = pos_at(track, ev["time"])
            if p is None:
                continue
            x, y = p.x, p.y
        attacker = ev.get("attacker")
        track.deaths.append(DeathEvent(
            time=ev["time"], x=x, y=y,
            gold_lost=ev.get("goldLost") or 0.0,
            gold_fed=ev.get("goldFed") or 0.0,
            time_dead=ev.get("timeDead") or 0.0,
            killer=attacker if attacker and attacker > 0 else None,
            assists=[a for a in (ev.get("assist") or []) if a and a > 0],
            flags={k: bool(ev.get(k)) for k in _FLAG_FIELDS if ev.get(k) is not None},
        ))
    return track


def _to_match(match_id: int, raw: dict) -> MatchData:
    parsed = raw.get("parsedDateTime") is not None
    players = []
    for p in (raw.get("players") or []):
        pdata = p.get("playbackData") or {}
        players.append(_parse_track(
            pdata, p["heroId"], p["isRadiant"], p.get("steamAccountId")))
    # wards/buildings are attached from OpenDota in main.py
    return MatchData(match_id=match_id, parsed=parsed,
                     radiant_win=raw.get("didRadiantWin"),
                     players=players)


def fetch_match(match_id: int, token: str | None = None,
                wait: bool = True, poll_s: int = 15, timeout_s: int = 300,
                log=print) -> MatchData:
    """Fetch a match, nudging STRATZ to parse it if needed, then polling."""
    token = token or os.environ.get("STRATZ_TOKEN")
    if not token:
        raise RuntimeError("Set STRATZ_TOKEN (see .env.example).")

    md = _to_match(match_id, _post(token, {"id": match_id}))
    if md.parsed or not wait:
        return md

    # Not parsed — ask STRATZ to download + parse it now, then wait.
    if request_download(match_id, token):
        log("Asked STRATZ to parse this match — this can take a minute…")
    else:
        log("Waiting for STRATZ to parse this match…")

    deadline = time.time() + timeout_s
    while True:
        time.sleep(poll_s)
        md = _to_match(match_id, _post(token, {"id": match_id}))
        if md.parsed:
            return md
        if time.time() > deadline:
            raise TimeoutError(
                "STRATZ could not parse this match in time. If it's more than "
                "~2 weeks old the replay has expired and can't be parsed anymore; "
                "otherwise try again in a few minutes.")
        log(f"Still parsing… retrying in {poll_s}s")
