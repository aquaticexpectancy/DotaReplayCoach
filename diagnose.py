"""Diagnose *why* a death happened, and what the player keeps repeating.

Two layers:
  movement()  — per death: did you never see it coming (awareness), or see it
                and fail to get out (execution)? Derived from whether you were
                still moving toward the threat in the seconds before you died.
  habits()    — across all deaths: the recurring pattern. One death is noise;
                the same shape three times is a habit worth fixing.

Everything here is computed from recorded data. No speculation.
"""
from __future__ import annotations
import math

import config as C
import items as items_mod
from positions import pos_at, dist, hp_at
from models import Position

WINDOW = 8.0        # seconds before death we examine
STILL = 0.25        # grid units/sec below this counts as "not really moving"
LOW_HP = 0.45       # fraction of max hp that counts as "already low"
ESCAPE_WINDOW = 10.0


# ---------------------------------------------------------------- movement

def _threat_tracks(match, me, a) -> list:
    """Heroes credited with the kill; fall back to enemies near the death."""
    d = a.death
    ids = set()
    if d and d.killer:
        ids.add(d.killer)
    if d and d.assists:
        ids.update(d.assists)
    tracks = [h for h in match.enemies_of(me) if h.hero_id in ids]
    if tracks:
        return tracks
    out = []
    for h in match.enemies_of(me):
        p = pos_at(h, a.time)
        if p and dist(a.me, p) < C.NEAR_ENEMY:
            out.append(h)
    return out


def _centroid(tracks, t) -> Position | None:
    pts = [p for p in (pos_at(h, t) for h in tracks) if p]
    if not pts:
        return None
    return Position(t, sum(p.x for p in pts) / len(pts),
                    sum(p.y for p in pts) / len(pts))


def movement(match, me, a) -> dict | None:
    """Awareness vs execution, from the player's motion relative to the threat.

    radial > 0 means the player moved *toward* the threat that second.
    """
    threats = _threat_tracks(match, me, a)
    if not threats:
        return None
    t = a.time
    samples = []                      # (seconds_before_death, radial)
    for k in range(int(WINDOW), 0, -1):
        t0, t1 = t - k, t - k + 1
        p0, p1 = pos_at(me, t0), pos_at(me, t1)
        c0 = _centroid(threats, t0)
        if not (p0 and p1 and c0):
            continue
        ux, uy = c0.x - p0.x, c0.y - p0.y
        n = math.hypot(ux, uy)
        if n < 1e-6:
            continue
        vx, vy = p1.x - p0.x, p1.y - p0.y
        samples.append((k, (vx * ux + vy * uy) / n))
    if not samples:
        return None

    # When did they last commit to leaving? (sustained non-approach to the end)
    reaction = None
    for k, r in samples:                       # samples run oldest -> newest
        if r < -STILL and all(rr <= STILL for kk, rr in samples if kk <= k):
            reaction = float(k)
            break
    approached = sum(r for _, r in samples if r > STILL)
    fled = -sum(r for _, r in samples if r < -STILL)
    moved = approached + fled

    flags = (a.death.flags if a.death else {}) or {}
    tried_tp = bool(flags.get("isAttemptTpOut"))
    died_back = bool(flags.get("isDieBack"))
    burst = bool(flags.get("isBurst"))

    # Already low on hp before the fight started?
    row = hp_at(me, t - WINDOW)
    low_before = bool(row and row[1] and row[0] / row[1] < LOW_HP)

    # A teamfight death is a different question entirely — advancing on the enemy
    # is the *intent* there, so "you never saw it coming" would be nonsense.
    if a.label == "Lost teamfight":
        if reaction is None:
            return {"kind": "committed", "title": "You committed to the fight",
                    "text": ("You stayed in until you died. The question here isn't "
                             "awareness — it's whether this fight was worth taking, and "
                             "where you stood inside it."
                             + (" You were bursted down fast." if burst else "")),
                    "reaction": None, "approached": round(approached, 2),
                    "lowBefore": low_before}
        return {"kind": "committed", "title": "You tried to disengage",
                "text": (f"You started pulling out about {reaction:.0f}s before dying but "
                         "couldn't get clear — disengage is the skill to work on here."),
                "reaction": reaction, "approached": round(approached, 2),
                "lowBefore": low_before}

    if reaction is None and not (tried_tp or died_back):
        if approached > STILL and approached >= fled:
            kind, title = "awareness", "You never saw it coming"
            text = ("You kept moving toward them right up to the moment you died — "
                    "no retreat at any point in the last 8 seconds. This is a "
                    "map-awareness miss, not a mechanical one.")
        elif moved < STILL:
            kind, title = "awareness", "You stood still as they closed"
            text = ("You barely moved in the last few seconds while they closed in — "
                    "you were focused on farming/fighting, not on the threat.")
        else:
            kind, title = "awareness", "No real attempt to leave"
            text = "There's no retreat in your movement before this death."
    else:
        lead = reaction if reaction is not None else 0.0
        if tried_tp:
            kind, title = "execution", "You reacted, but too late to TP"
            text = (f"You tried to teleport out — the channel never finished. "
                    f"The exit needed to start earlier.")
        elif lead >= 3:
            kind, title = "execution", "You saw it and still couldn't escape"
            text = (f"You began retreating about {lead:.0f}s before dying and died "
                    "anyway — you needed to leave sooner, or had no mobility to make it.")
        else:
            kind, title = "execution", "You reacted too late"
            text = (f"You only started moving away ~{max(lead, 0.5):.1f}s before you "
                    "died. The read was right but came a beat late.")
        if died_back:
            text += " You died with your back turned."

    if burst:
        text += " You were bursted down, so once they connected there was little time to answer."
    if low_before:
        text += " You were already below 45% HP when this started."

    return {"kind": kind, "title": title, "text": text,
            "reaction": reaction, "approached": round(approached, 2),
            "lowBefore": low_before}


# ------------------------------------------------------------------ habits

def _escape_unused(me, a, item_db) -> bool:
    """Had a real escape item and never pressed it in the last 10s.

    Deliberately excludes TP scrolls and other consumables: a purchase log can't
    tell us whether one is still in the bag, and you rarely TP out of a gank
    anyway — so counting them makes this fire on every death and mean nothing.
    Blink/Force are permanent, so "bought it before t" really does mean "had it".
    """
    t = a.time
    keys = items_mod.BLINK_KEYS | items_mod.FORCE_KEYS
    if not items_mod.has_any(me.purchase_log, t, keys):
        return False
    for ct, iid, _tgt in me.item_casts:
        if t - ESCAPE_WINDOW <= ct <= t:
            k = (item_db.get(str(iid)) or {}).get("key")
            if k in keys:
                return False
    return True


def habits(match, me, analyses, diags: dict, item_db: dict, hname) -> dict:
    """Roll per-death findings into the player's repeating pattern."""
    n = len(analyses)
    # weight breaks ties toward the more specific / more actionable pattern,
    # so a circumstantial one never outranks a diagnostic one on equal counts.
    rules = [
        ("unaware", "Caught unaware", 3,
         lambda a: (diags.get(a.index) or {}).get("kind") == "awareness",
         "with no reaction to the threat at all"),
        ("escape_unused", "Escape left unused", 3,
         lambda a: _escape_unused(me, a, item_db),
         "with a Blink or Force Staff available that you never used"),
        ("alone", "Caught alone", 2,
         lambda a: a.features.get("nearest_ally", 0) > C.ISOLATED_ALLY,
         "isolated, with no ally close enough to help"),
        ("low_hp", "Fighting on low HP", 2,
         lambda a: (diags.get(a.index) or {}).get("lowBefore"),
         "already below 45% HP before the fight even started"),
        ("deep_blind", "Deep with no vision", 1,
         lambda a: a.features.get("in_enemy_half") and not a.features.get("warded"),
         "on the enemy half of the map with no Observer covering you"),
    ]

    patterns = []
    for key, title, weight, pred, phrase in rules:
        hit = [a for a in analyses if pred(a)]
        if not hit:
            continue
        gold = sum(int((a.death.gold_lost if a.death else 0) or 0) for a in hit)
        patterns.append({
            "key": key, "title": title, "phrase": phrase, "weight": weight,
            "count": len(hit), "total": n, "gold": gold,
            "deaths": sorted(a.index for a in hit),
        })
    patterns.sort(key=lambda p: (p["count"], p["weight"], p["gold"]), reverse=True)

    # Same hero killing you repeatedly is its own, very personal tell.
    tally: dict[int, int] = {}
    for a in analyses:
        if a.death and a.death.killer:
            tally[a.death.killer] = tally.get(a.death.killer, 0) + 1
    nemesis = None
    if tally:
        hid, cnt = max(tally.items(), key=lambda kv: kv[1])
        if cnt >= 2:
            nemesis = {"heroId": hid, "name": hname(hid)[0], "count": cnt}

    head = next((p for p in patterns if p["count"] >= 2), None)
    if head:
        gold = f" — about {head['gold']:,} gold" if head["gold"] else ""
        headline = {
            **head,
            "text": (f"{head['count']} of your {n} deaths came {head['phrase']}"
                     f"{gold}. Fix this one pattern and most of these stop happening."),
        }
    else:
        headline = {
            "key": "varied", "title": "No single repeating pattern",
            "count": 0, "total": n, "gold": 0, "deaths": [],
            "text": ("Your deaths don't share one obvious cause this game — review "
                     "them individually rather than chasing one habit."),
        }
    return {"headline": headline, "patterns": patterns, "nemesis": nemesis}
