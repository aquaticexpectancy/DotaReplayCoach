"""The death-mistake detector. Turns each death into features -> a label + score.

This is the heart of the project. Everything upstream just feeds it positions;
everything downstream (coach, card) just presents its output.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import config as C
from models import MatchData, HeroTrack, DeathEvent, Position
from positions import dist, pos_at, last_seen, active_wards, alive


@dataclass
class DeathAnalysis:
    index: int                 # which death of the player (0-based)
    time: float
    label: str
    score: float               # higher = more teachable / worse mistake
    death: DeathEvent | None = None      # raw event (gold, flags, killer)
    features: dict = field(default_factory=dict)
    me: Position | None = None
    allies: list = field(default_factory=list)   # (hero_id, Position) at death
    enemies: list = field(default_factory=list)  # (hero_id, Position) at death


def _features(match: MatchData, me: HeroTrack, death: DeathEvent) -> dict:
    t = death.time
    my_pos = Position(t, death.x, death.y)

    allies = [(h.hero_id, pos_at(h, t)) for h in match.team_of(me) if alive(h, t)]
    enemies_alive = [h for h in match.enemies_of(me) if alive(h, t)]
    enemy_pos = [(h.hero_id, pos_at(h, t)) for h in enemies_alive]
    allies = [(hid, p) for hid, p in allies if p]
    enemy_pos = [(hid, p) for hid, p in enemy_pos if p]

    nearest_ally = min((dist(my_pos, p) for _, p in allies), default=float("inf"))
    enemies_near = sum(dist(my_pos, p) < C.NEAR_ENEMY for _, p in enemy_pos)

    # "no enemies visible" proxy: how many of the gankers were FAR away a few
    # seconds earlier -> the player had no recent info on them.
    gankers_were_far = 0
    for h in enemies_alive:
        recent = last_seen(h, t - C.ROTATE_LOOKBACK)
        if recent and dist(my_pos, recent) > C.FAR_LASTSEEN:
            gankers_were_far += 1

    warded = any(dist(my_pos, Position(t, w.x, w.y)) < C.WARD_COVER
                 for w in active_wards(match.wards, t, me.is_radiant))

    dead_b = match.dead_buildings(t)
    near_enemy_tower = any(
        b.is_radiant != me.is_radiant and b.kind == "tower"
        and b.key not in dead_b
        and dist(my_pos, Position(t, b.x, b.y)) < C.TOWER_RANGE
        for b in match.buildings
    )

    return {
        "nearest_ally": round(nearest_ally, 1),
        "enemies_near": enemies_near,
        "enemies_dead": len(match.enemies_of(me)) - len(enemies_alive),
        "gankers_were_far": gankers_were_far,
        "warded": warded,
        "in_enemy_half": C.on_enemy_half(death.x, death.y, me.is_radiant),
        "near_enemy_tower": near_enemy_tower,
        "gold_lost": death.gold_lost,
        "_allies": allies,
        "_enemies": enemy_pos,
        "_me": my_pos,
    }


def _classify(f: dict) -> tuple[str, float]:
    solo = f["nearest_ally"] > C.ISOLATED_ALLY
    ganked = f["enemies_near"] >= 2
    blind = f["gankers_were_far"] >= 3 and not f["warded"]

    # score rewards isolated + blind + costly deaths (the teachable ones)
    score = 0.0
    if solo: score += 2
    if blind: score += 3
    if not f["warded"]: score += 1
    if f["in_enemy_half"]: score += 1
    score += min(f["gold_lost"] / 300.0, 3)

    if f["near_enemy_tower"] and ganked:
        return "Dove enemy tower", score + 1
    if ganked and blind and f["in_enemy_half"]:
        return "Pushed into a gank (no info)", score + 1   # the classic "death 3"
    if solo and f["in_enemy_half"] and ganked:
        return "Overextended pickoff", score
    if not f["warded"] and solo:
        return "Caught with no vision", score
    if f["enemies_near"] >= 3 and len(f["_allies"]) >= 2:
        return "Lost teamfight", score * 0.4            # not a solo error, deprioritize
    return "Death", score * 0.5


def analyze_match(match: MatchData, me: HeroTrack) -> list[DeathAnalysis]:
    out: list[DeathAnalysis] = []
    for i, death in enumerate(me.deaths):
        f = _features(match, me, death)
        label, score = _classify(f)
        out.append(DeathAnalysis(
            index=i, time=death.time, label=label, score=round(score, 2),
            death=death,
            features={k: v for k, v in f.items() if not k.startswith("_")},
            me=f["_me"], allies=f["_allies"], enemies=f["_enemies"],
        ))
    out.sort(key=lambda d: d.score, reverse=True)
    return out
