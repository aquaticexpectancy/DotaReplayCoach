"""Position/time lookups over a HeroTrack's sampled positions."""
from __future__ import annotations
import bisect
import math
from models import HeroTrack, Position, Ward


def dist(a: Position, b: Position) -> float:
    return math.hypot(a.x - b.x, a.y - b.y)


def _times(track: HeroTrack) -> list[float]:
    # cache the time index on the track object to avoid rebuilding per lookup
    cache = getattr(track, "_tcache", None)
    if cache is None or len(cache) != len(track.positions):
        cache = [p.time for p in track.positions]
        setattr(track, "_tcache", cache)
    return cache


def pos_at(track: HeroTrack, t: float) -> Position | None:
    """Nearest sampled position to time t (both directions)."""
    if not track.positions:
        return None
    times = _times(track)
    i = bisect.bisect_left(times, t)
    if i == 0:
        return track.positions[0]
    if i >= len(times):
        return track.positions[-1]
    before, after = track.positions[i - 1], track.positions[i]
    return before if (t - before.time) <= (after.time - t) else after


def last_seen(track: HeroTrack, t: float) -> Position | None:
    """Most recent position at or before t (a stand-in for 'last known location')."""
    if not track.positions:
        return None
    times = _times(track)
    i = bisect.bisect_right(times, t) - 1
    return track.positions[i] if i >= 0 else track.positions[0]


def active_wards(wards: list[Ward], t: float, radiant: bool,
                 kind: str | None = "observer") -> list[Ward]:
    """Active wards for one team at time t. Defaults to observers only —
    sentries carry no vision, so they must not count as 'the area was warded'."""
    return [w for w in wards
            if w.is_radiant == radiant and w.time_from <= t <= w.time_to
            and (kind is None or getattr(w, "kind", "observer") == kind)]


def alive(track: HeroTrack, t: float) -> bool:
    """Dead between a death and its actual respawn (STRATZ timeDead)."""
    for d in track.deaths:
        dead_for = d.time_dead if getattr(d, "time_dead", 0) else 40
        if d.time <= t < d.time + dead_for:
            return False
    return True


def _latest(series: list, t: float):
    """Latest tuple (time, ...) at or before t from a time-sorted series."""
    if not series:
        return None
    lo, hi = 0, len(series) - 1
    if series[0][0] > t:
        return None
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if series[mid][0] <= t:
            lo = mid
        else:
            hi = mid - 1
    return series[lo]


def hp_at(track: HeroTrack, t: float):
    """(hp, maxHp, mp, maxMp) at time t, or None."""
    row = _latest(track.health, t)
    return row[1:] if row else None


def level_at(track: HeroTrack, t: float) -> int:
    row = _latest(track.levels, t)
    return row[1] if row else 1


def items_at(track: HeroTrack, t: float):
    """([main slot itemIds], neutralId) at time t."""
    row = _latest(track.inventory, t)
    return (row[1], row[2]) if row else ([], None)
