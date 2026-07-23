"""The death-mistake detector. Turns each death into features -> a label + score.

This is the heart of the project. Everything upstream just feeds it positions;
everything downstream (coach, card) just presents its output.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import abilities as abilities_mod
import config as C
import status as status_mod
from models import MatchData, HeroTrack, DeathEvent, Position
from positions import dist, pos_at, last_seen, active_wards, alive

LANING_UNTIL = 600.0        # seconds; before this, "gank" framing is wrong


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


def _features(match: MatchData, me: HeroTrack, death: DeathEvent,
              ab_meta: dict) -> dict:
    t = death.time
    my_pos = Position(t, death.x, death.y)

    allies = [(h.hero_id, pos_at(h, t)) for h in match.team_of(me) if alive(h, t)]
    enemies_alive = [h for h in match.enemies_of(me) if alive(h, t)]
    enemy_pos = [(h.hero_id, pos_at(h, t)) for h in enemies_alive]
    allies = [(hid, p) for hid, p in allies if p]
    enemy_pos = [(hid, p) for hid, p in enemy_pos if p]

    nearest_ally = min((dist(my_pos, p) for _, p in allies), default=float("inf"))
    enemies_near = sum(dist(my_pos, p) < C.NEAR_ENEMY for _, p in enemy_pos)

    # A rotation means someone who was FAR is now ON you. Counting every distant
    # enemy (as this used to) made the three heroes standing in their own lanes
    # look like a gank squad — which fired "unseen gank" on every laning death.
    gankers_were_far = 0
    for h in enemies_alive:
        here = pos_at(h, t)
        if not here or dist(my_pos, here) >= C.NEAR_ENEMY:
            continue                      # not part of what killed you
        recent = last_seen(h, t - C.ROTATE_LOOKBACK)
        if recent and dist(my_pos, recent) > C.FAR_LASTSEEN:
            gankers_were_far += 1

    # Were you physically yanked out of position (hook, skewer, lasso...)?
    # Two guards matter: the cast must land BEFORE the killing blow (a hook that
    # lands at +0s did not cause the death), and your position must actually
    # jump afterwards — otherwise it missed you.
    displaced_by = None
    for h in match.enemies_of(me):
        for ct, aid, _tgt in h.ability_casts:
            if not (t - C.DISPLACE_WINDOW <= ct <= t - C.DISPLACE_MIN_LEAD):
                continue
            key = (abilities_mod.info(aid, ab_meta) or {}).get("key") or ""
            if key not in status_mod.PULLS_VICTIM:
                continue
            before, after = pos_at(me, ct), pos_at(me, ct + 1.5)
            caster = pos_at(h, ct)
            if not (before and after and caster):
                continue
            # A pull both moves you and closes the gap to the caster. Requiring
            # both separates a landed hook from you simply walking (1s position
            # sampling only captures part of the drag, so the bar must be low).
            moved = dist(before, after)
            closed = dist(before, caster) - dist(after, caster)
            if moved >= C.DISPLACE_MIN_MOVE and closed >= C.DISPLACE_MIN_CLOSE:
                displaced_by = status_mod.PULLS_VICTIM[key]
                break
        if displaced_by:
            break

    dead_b = match.dead_buildings(t)
    # Your own standing towers grant vision too — a death in tower range is not
    # a "no vision" death, so count that before flagging the finding.
    in_tower_vision = any(
        b.is_radiant == me.is_radiant and b.kind == "tower" and b.key not in dead_b
        and dist(my_pos, Position(t, b.x, b.y)) < C.TOWER_VISION
        for b in match.buildings
    )
    warded = in_tower_vision or any(
        dist(my_pos, Position(t, w.x, w.y)) < C.WARD_COVER
        for w in active_wards(match.wards, t, me.is_radiant))

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
        "displaced_by": displaced_by,
        "laning": t < LANING_UNTIL,
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

    # Being yanked out of position outranks everything else — you didn't walk
    # anywhere, so no "overextended"/"gank" framing applies.
    if f.get("displaced_by"):
        return f"{f['displaced_by']} out of position", score + 1
    if f["near_enemy_tower"] and ganked:
        return "Dove enemy tower", score + 1
    # A "gank" needs an actual rotation onto you. During laning, deaths are
    # lane fights or pulls, not ganks — don't dress them up as one.
    if ganked and blind and f["in_enemy_half"] and not f.get("laning"):
        return "Pushed into a gank (no info)", score + 1
    if solo and f["in_enemy_half"] and ganked:
        return "Overextended pickoff", score
    if not f["warded"] and solo:
        return "Caught with no vision", score
    if f["enemies_near"] >= 3 and len(f["_allies"]) >= 2:
        return "Lost teamfight", score * 0.4            # not a solo error, deprioritize
    return "Death", score * 0.5


def analyze_match(match: MatchData, me: HeroTrack) -> list[DeathAnalysis]:
    out: list[DeathAnalysis] = []
    ab_meta = abilities_mod.load_meta()
    for i, death in enumerate(me.deaths):
        f = _features(match, me, death, ab_meta)
        label, score = _classify(f)
        out.append(DeathAnalysis(
            index=i, time=death.time, label=label, score=round(score, 2),
            death=death,
            features={k: v for k, v in f.items() if not k.startswith("_")},
            me=f["_me"], allies=f["_allies"], enemies=f["_enemies"],
        ))
    out.sort(key=lambda d: d.score, reverse=True)
    return out
