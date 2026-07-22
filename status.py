"""Infer short-lived status effects from ability/item casts.

STRATZ does not expose a reliable per-frame modifier track for most matches, so
we reconstruct important buffs and crowd-control windows from cast events.
"""
from __future__ import annotations

from positions import pos_at, dist
from models import MatchData, HeroTrack

# kind -> (label, color, polarity)  polarity: bad | good
STATUS_META = {
    # Crowd control / debuffs
    "stun": ("STUN", "#fbbf24", "bad"),
    "hex": ("HEX", "#c084fc", "bad"),
    "silence": ("SILENCE", "#60a5fa", "bad"),
    "root": ("ROOT", "#fb923c", "bad"),
    "taunt": ("TAUNT", "#f87171", "bad"),
    "sleep": ("SLEEP", "#a78bfa", "bad"),
    "break": ("BREAK", "#f472b6", "bad"),
    "disarm": ("DISARM", "#94a3b8", "bad"),
    "fear": ("FEAR", "#e879f9", "bad"),
    "cyclone": ("CYCLONE", "#7dd3fc", "bad"),
    "mute": ("MUTE", "#818cf8", "bad"),
    "ethereal": ("GHOST", "#67e8f9", "bad"),  # usually hostile; self Ghost Scepter is good
    # Positive buffs
    "bkb": ("BKB", "#fde68a", "good"),
    "haste": ("HASTE", "#4ade80", "good"),
    "invis": ("INVIS", "#a3e635", "good"),
    "phase": ("PHASE", "#86efac", "good"),
    "heal": ("HEAL", "#34d399", "good"),
    "armor": ("ARMOR", "#2dd4bf", "good"),
    "shield": ("SHIELD", "#5eead4", "good"),
    "return": ("RETURN", "#fcd34d", "good"),
    "lifesteal": ("LIFESTEAL", "#fb7185", "good"),
    "immune": ("SPELL IMMUNE", "#fde68a", "good"),
    "ghost_self": ("GHOST", "#99f6e4", "good"),
    "empower": ("BUFF", "#86efac", "good"),
    "grave": ("GRAVE", "#6ee7b7", "good"),
}

# ability key -> (status_kind, duration_s, apply_mode)
# modes: target | self | aoe | both | ally | aoe_ally
_ABILITY_STATUS = {
    # --- hard CC ---
    "legion_commander_duel": ("taunt", 5.0, "both"),
    "warlock_rain_of_chaos": ("stun", 1.3, "aoe"),
    "nevermore_requiem": ("fear", 1.8, "aoe"),
    "dawnbreaker_solar_guardian": ("stun", 1.6, "aoe"),
    "witch_doctor_paralyzing_cask": ("stun", 0.8, "target"),
    "shadow_shaman_voodoo": ("hex", 2.6, "target"),
    "shadow_shaman_shackles": ("root", 3.0, "target"),
    "bane_fiends_grip": ("stun", 5.0, "target"),
    "bane_nightmare": ("sleep", 4.0, "target"),
    "lion_voodoo": ("hex", 2.8, "target"),
    "lion_impale": ("stun", 1.4, "aoe"),
    "ogre_magi_fireblast": ("stun", 1.5, "target"),
    "ogre_magi_unrefined_fireblast": ("stun", 1.5, "target"),
    "sven_storm_bolt": ("stun", 1.5, "aoe"),
    "skeleton_king_hellfire_blast": ("stun", 2.0, "target"),
    "dragon_knight_dragon_tail": ("stun", 2.0, "target"),
    "earthshaker_fissure": ("stun", 1.0, "aoe"),
    "earthshaker_echo_slam": ("stun", 0.3, "aoe"),
    "tidehunter_ravage": ("stun", 2.4, "aoe"),
    "axe_berserkers_call": ("taunt", 2.4, "aoe"),
    "beastmaster_primal_roar": ("stun", 3.0, "target"),
    "rattletrap_hookshot": ("stun", 1.5, "target"),
    "jakiro_ice_path": ("stun", 1.5, "aoe"),
    "crystal_maiden_frostbite": ("root", 2.5, "target"),
    "naga_siren_ensnare": ("root", 3.5, "target"),
    "meepo_earthbind": ("root", 2.0, "aoe"),
    "treant_overgrowth": ("root", 3.0, "aoe"),
    "ember_spirit_searing_chains": ("root", 2.0, "aoe"),
    "skywrath_mage_ancient_seal": ("silence", 4.0, "target"),
    "death_prophet_silence": ("silence", 4.0, "aoe"),
    "silencer_global_silence": ("silence", 4.5, "aoe"),
    "doom_bringer_doom": ("mute", 16.0, "target"),
    "vengefulspirit_magic_missile": ("stun", 1.5, "target"),
    "faceless_void_chronosphere": ("stun", 4.0, "aoe"),
    "winter_wyvern_winters_curse": ("taunt", 4.0, "aoe"),
    # --- positive buffs ---
    "legion_commander_press_the_attack": ("haste", 5.0, "ally"),
    "ogre_magi_bloodlust": ("haste", 30.0, "ally"),
    "dark_seer_surge": ("haste", 6.0, "ally"),
    "windrunner_windrun": ("haste", 3.0, "self"),
    "weaver_shukuchi": ("invis", 4.0, "self"),
    "bounty_hunter_wind_walk": ("invis", 20.0, "self"),
    "clinkz_wind_walk": ("invis", 25.0, "self"),
    "invoker_ghost_walk": ("invis", 100.0, "self"),
    "mirana_invis": ("invis", 15.0, "aoe_ally"),
    "riki_blink_strike": ("invis", 0.0, "self"),  # skip
    "nyx_assassin_vendetta": ("invis", 40.0, "self"),
    "slark_shadow_dance": ("invis", 4.0, "self"),
    "sandking_sand_storm": ("invis", 0.0, "self"),
    "omniknight_guardian_angel": ("immune", 6.0, "aoe_ally"),
    "omniknight_purification": ("heal", 0.2, "ally"),
    "omniknight_repel": ("bkb", 10.0, "ally"),  # Heavenly Grace-ish older name
    "omniknight_martyr": ("heal", 6.0, "ally"),
    "abaddon_aphotic_shield": ("shield", 15.0, "ally"),
    "dazzle_shallow_grave": ("grave", 5.0, "ally"),
    "oracle_false_promise": ("grave", 8.0, "ally"),
    "winter_wyvern_cold_embrace": ("heal", 4.0, "ally"),
    "treant_living_armor": ("armor", 15.0, "ally"),
    "lich_frost_shield": ("armor", 6.0, "ally"),
    "sven_gods_strength": ("empower", 10.0, "self"),
    "sven_warcry": ("armor", 8.0, "aoe_ally"),
    "magnataur_empower": ("empower", 30.0, "ally"),
    "lycan_howl": ("armor", 8.0, "aoe_ally"),
    "beastmaster_inner_beast": ("empower", 0.0, "aoe_ally"),
    "luna_lunar_blessing": ("empower", 0.0, "aoe_ally"),
    "juggernaut_healing_ward": ("heal", 25.0, "self"),
    "witch_doctor_voodoo_restoration": ("heal", 0.0, "self"),
    "necrolyte_death_pulse": ("heal", 0.2, "aoe_ally"),
    "chen_hand_of_god": ("heal", 0.2, "aoe_ally"),
    "dawnbreaker_luminosity": ("lifesteal", 0.0, "self"),
    "huskar_life_break": ("lifesteal", 0.0, "self"),
    "life_stealer_rage": ("bkb", 5.0, "self"),
    "juggernaut_blade_fury": ("bkb", 5.0, "self"),
    "ember_spirit_flame_guard": ("shield", 12.0, "self"),
    "phoenix_fire_spirits": ("empower", 16.0, "self"),
    "keeper_of_the_light_chakra_magic": ("empower", 0.2, "ally"),
    "rubick_null_field": ("armor", 0.0, "aoe_ally"),
    "vengefulspirit_wave_of_terror": ("armor", 0.0, "aoe"),  # debuff armor — skip via 0
    "ancient_apparition_ice_blast": ("break", 0.0, "target"),
}

_ITEM_STATUS = {
    # Debuff items
    "orchid": ("silence", 5.0, "target"),
    "bloodthorn": ("silence", 5.0, "target"),
    "sheepstick": ("hex", 3.5, "target"),
    "abyssal_blade": ("stun", 2.0, "target"),
    "basher": ("stun", 1.2, "target"),
    "nullifier": ("mute", 5.0, "target"),
    "ethereal_blade": ("ethereal", 4.0, "target"),
    "heavens_halberd": ("disarm", 3.0, "target"),
    "cyclone": ("cyclone", 2.5, "target"),
    "wind_waker": ("cyclone", 2.5, "target"),
    "gungir": ("root", 2.0, "aoe"),
    "rod_of_atos": ("root", 2.0, "target"),
    # Positive items
    "black_king_bar": ("bkb", 9.0, "self"),
    "blade_mail": ("return", 4.2, "self"),
    "ghost": ("ghost_self", 4.0, "self"),
    "glimmer_cape": ("invis", 5.0, "ally"),
    # phase_boots intentionally omitted — a movement steroid, not a threat worth flagging.
    "mask_of_madness": ("haste", 8.0, "self"),
    "satanic": ("lifesteal", 6.0, "self"),
    "sange_and_yasha": ("haste", 0.0, "self"),
    "yasha_and_kaya": ("haste", 0.0, "self"),
    "pipe": ("shield", 12.0, "aoe_ally"),
    "crimson_guard": ("armor", 12.0, "aoe_ally"),
    "lotus_orb": ("shield", 6.0, "ally"),
    "sphere": ("shield", 0.2, "self"),  # Linken's block event not tracked well
    "solar_crest": ("armor", 12.0, "ally"),
    "medallion_of_courage": ("armor", 12.0, "ally"),
    "holy_locket": ("heal", 0.2, "ally"),
    "mekansm": ("heal", 0.2, "aoe_ally"),
    "guardian_greaves": ("heal", 0.2, "aoe_ally"),
    "smoke_of_deceit": ("invis", 35.0, "aoe_ally"),
    "shadow_amulet": ("invis", 0.0, "ally"),
    "silver_edge": ("invis", 14.0, "self"),
    "invis_sword": ("invis", 14.0, "self"),
    "butterfly": ("haste", 0.0, "self"),
    "minotaur_horn": ("bkb", 2.0, "self"),
}

# Skip noisy item actives on the map ping layer (status still applies if listed above).
_ITEM_MAP_SKIP = {
    "tango", "clarity", "flask", "enchanted_mango", "faerie_fire",
    "ward_observer", "ward_sentry", "tpscroll", "magic_stick", "magic_wand",
    "power_treads", "arcane_boots", "tranquil_boots",
    "quelling_blade", "branches", "blood_grenade", "dust",
    "madstone_bundle", "famango", "great_famango",
    "phase_boots",  # status shown; map ping would spam
}

AOE_RADIUS = 8.0  # ~1024 world units on the 128-grid


def _hero_by_id(match: MatchData, hid: int | None) -> HeroTrack | None:
    if not hid:
        return None
    for p in match.players:
        if p.hero_id == hid:
            return p
    return None


def _near_enemies(match: MatchData, caster: HeroTrack, t: float,
                  radius: float = AOE_RADIUS) -> list[HeroTrack]:
    cpos = pos_at(caster, t)
    if not cpos:
        return []
    out = []
    for h in match.enemies_of(caster):
        p = pos_at(h, t)
        if p and dist(cpos, p) <= radius:
            out.append(h)
    return out


def _near_allies(match: MatchData, caster: HeroTrack, t: float,
                 radius: float = AOE_RADIUS, include_self: bool = True) -> list[HeroTrack]:
    cpos = pos_at(caster, t)
    if not cpos:
        return [caster] if include_self else []
    out = [caster] if include_self else []
    for h in match.team_of(caster):
        p = pos_at(h, t)
        if p and dist(cpos, p) <= radius:
            out.append(h)
    return out


def _apply(intervals: dict[int, list], victim: HeroTrack, kind: str,
           t0: float, dur: float, source: str):
    if dur <= 0.05:
        return
    meta = STATUS_META.get(kind)
    if not meta:
        return
    label, color, polarity = meta
    intervals.setdefault(victim.hero_id, []).append({
        "kind": kind,
        "label": label,
        "color": color,
        "polarity": polarity,
        "t0": t0,
        "t1": t0 + dur,
        "source": source,
    })


def _dispatch(intervals, match, caster, kind, dur, mode, source, ct, target,
              hostile_fallback: bool):
    if mode == "self":
        _apply(intervals, caster, kind, ct, dur, source)
    elif mode == "target":
        vic = _hero_by_id(match, target)
        if vic:
            _apply(intervals, vic, kind, ct, dur, source)
        elif not target and hostile_fallback:
            near = _near_enemies(match, caster, ct, radius=8.0)
            if near:
                _apply(intervals, near[0], kind, ct, dur, source)
    elif mode == "both":
        _apply(intervals, caster, kind, ct, dur, source)
        vic = _hero_by_id(match, target)
        if vic:
            _apply(intervals, vic, kind, ct, dur, source)
    elif mode == "aoe":
        for vic in _near_enemies(match, caster, ct):
            _apply(intervals, vic, kind, ct, dur, source)
    elif mode == "ally":
        vic = _hero_by_id(match, target)
        if vic and vic.is_radiant == caster.is_radiant:
            _apply(intervals, vic, kind, ct, dur, source)
        elif not vic or vic is caster or not target:
            _apply(intervals, caster, kind, ct, dur, source)
        elif vic and vic.is_radiant != caster.is_radiant:
            # Mis-tagged hostile target on a support spell — still show on self.
            _apply(intervals, caster, kind, ct, dur, source)
    elif mode == "aoe_ally":
        for vic in _near_allies(match, caster, ct):
            _apply(intervals, vic, kind, ct, dur, source)


def build_status_intervals(match: MatchData, ab_meta: dict, item_db: dict,
                           t0: float, t1: float) -> dict[int, list]:
    """hero_id -> list of status intervals overlapping the playback window."""
    intervals: dict[int, list] = {}
    pad = 6.0
    for caster in match.players:
        for ct, aid, target in caster.ability_casts:
            if ct < t0 - pad or ct > t1 + 0.5:
                continue
            info = ab_meta.get(str(aid)) or {}
            key = info.get("key") or ""
            rule = _ABILITY_STATUS.get(key)
            if not rule:
                continue
            kind, dur, mode = rule
            source = info.get("name") or key
            _dispatch(intervals, match, caster, kind, dur, mode, source, ct,
                      target, hostile_fallback=False)

        for ct, iid, target in caster.item_casts:
            if ct < t0 - pad or ct > t1 + 0.5:
                continue
            meta = item_db.get(str(iid)) or {}
            key = meta.get("key") or ""
            rule = _ITEM_STATUS.get(key)
            if not rule:
                continue
            kind, dur, mode = rule
            source = meta.get("name") or key
            hostile = mode in ("target", "aoe") and kind in {
                "stun", "hex", "silence", "root", "mute", "disarm", "ethereal",
                "cyclone", "fear", "taunt", "sleep", "break",
            }
            _dispatch(intervals, match, caster, kind, dur, mode, source, ct,
                      target, hostile_fallback=hostile)
    return intervals


def statuses_at(intervals: dict[int, list], hero_id: int, t: float) -> list[dict]:
    rows = []
    seen = set()
    for s in intervals.get(hero_id) or []:
        if s["t0"] - 0.05 <= t <= s["t1"] + 0.05:
            if s["kind"] in seen:
                continue
            seen.add(s["kind"])
            rem = max(0.0, s["t1"] - t)
            rows.append({
                "kind": s["kind"], "label": s["label"], "color": s["color"],
                "polarity": s.get("polarity") or "bad",
                "source": s["source"], "rem": round(rem, 1),
            })
    # Hard CC first, then other debuffs, then positive buffs.
    order = [
        "taunt", "stun", "hex", "sleep", "cyclone", "root", "silence", "mute",
        "fear", "break", "disarm", "ethereal",
        "bkb", "immune", "grave", "invis", "ghost_self", "return", "haste",
        "phase", "shield", "armor", "heal", "lifesteal", "empower",
    ]
    rows.sort(key=lambda r: order.index(r["kind"]) if r["kind"] in order else 99)
    return rows[:5]


def build_item_map_events(match: MatchData, me: HeroTrack, t0: float, t1: float,
                          item_db: dict, hmeta, project) -> list[dict]:
    events = []
    for tr in match.players:
        for ct, iid, _target in tr.item_casts:
            if ct < t0 - 0.05 or ct > t1 + 0.05:
                continue
            meta = item_db.get(str(iid)) or {}
            key = meta.get("key") or ""
            if not key or key in _ITEM_MAP_SKIP:
                continue
            pos = pos_at(tr, ct)
            if not pos:
                continue
            x, y = project(pos.x, pos.y)
            name, hic = hmeta(tr.hero_id)
            events.append({
                "t": round(ct, 2),
                "rel": round(ct - t1, 1),
                "heroId": tr.hero_id,
                "hero": name,
                "heroIcon": hic,
                "name": meta.get("name") or key.replace("_", " ").title(),
                "icon": meta.get("icon"),
                "key": key,
                "x": x, "y": y,
                "isRadiant": tr.is_radiant,
                "me": tr is me,
            })
    events.sort(key=lambda e: e["t"])
    return events
