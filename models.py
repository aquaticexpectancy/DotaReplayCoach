"""Typed containers. Kept deliberately small so the detector reads clearly."""
from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Position:
    time: float   # seconds from match start (can be negative pre-horn)
    x: float
    y: float


@dataclass
class DeathEvent:
    time: float
    x: float
    y: float
    gold_lost: float = 0.0    # STRATZ goldLost
    gold_fed: float = 0.0     # gold given to the killer(s)
    time_dead: float = 0.0    # respawn wait in seconds
    killer: int | None = None      # hero id, None if killed by creeps/tower
    assists: list[int] = field(default_factory=list)
    flags: dict = field(default_factory=dict)   # STRATZ analysis booleans


@dataclass
class Ward:
    x: float
    y: float
    time_from: float          # placed
    time_to: float            # expired / destroyed
    is_radiant: bool = True
    kind: str = "observer"    # observer | sentry


@dataclass
class Building:
    key: str            # npc_dota_goodguys_tower1_top
    x: float
    y: float
    icon: str           # sprite filename in assets/
    is_radiant: bool
    kind: str           # tower | rax | fort


@dataclass
class HeroTrack:
    hero_id: int
    is_radiant: bool
    account_id: int | None = None
    positions: list[Position] = field(default_factory=list)   # sorted by time
    deaths: list[DeathEvent] = field(default_factory=list)
    health: list = field(default_factory=list)     # (time, hp, maxHp, mp, maxMp)
    levels: list = field(default_factory=list)     # (time, level)
    inventory: list = field(default_factory=list)  # (time, [itemIds main slots], neutralId)
    ability_casts: list = field(default_factory=list)  # (time, ability_id, target)
    item_casts: list = field(default_factory=list)     # (time, item_id, target)
    # OpenDota per-minute economy (optional; attached in main.py)
    econ_times: list = field(default_factory=list)  # seconds: 0, 60, 120, ...
    lh_t: list = field(default_factory=list)        # cumulative last hits
    gold_t: list = field(default_factory=list)      # cumulative net worth-ish gold
    purchase_log: list = field(default_factory=list)  # [{time, key}, ...] OpenDota
    final_items: list = field(default_factory=list)   # end-game item ids item_0..5
    final_neutral: int | None = None


@dataclass
class MatchData:
    match_id: int
    parsed: bool
    radiant_win: bool | None = None
    players: list[HeroTrack] = field(default_factory=list)
    wards: list[Ward] = field(default_factory=list)
    buildings: list[Building] = field(default_factory=list)      # all 40, static positions
    building_kills: list = field(default_factory=list)           # [(time, key), ...]

    def dead_buildings(self, t: float) -> set[str]:
        """Keys of buildings destroyed strictly before time t.
        OpenDota reports both tier-4 towers under one key with no lane suffix,
        so the Nth kill of such a key maps to the Nth matching building."""
        counts: dict[str, int] = {}
        for kt, key in self.building_kills:
            if kt <= t:
                counts[key] = counts.get(key, 0) + 1
        dead: set[str] = set()
        for key, n in counts.items():
            matches = [b.key for b in self.buildings
                       if b.key == key or b.key.startswith(key + "_")]
            dead.update(matches[:n] if matches else [key])
        return dead

    def team_of(self, hero: HeroTrack) -> list[HeroTrack]:
        return [p for p in self.players if p.is_radiant == hero.is_radiant and p is not hero]

    def enemies_of(self, hero: HeroTrack) -> list[HeroTrack]:
        return [p for p in self.players if p.is_radiant != hero.is_radiant]
