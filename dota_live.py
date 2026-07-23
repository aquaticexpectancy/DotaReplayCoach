"""Valve's live Datafeed — patch-current hero/ability numbers.

This is the same source dota2.com itself renders from, so it tracks the live
patch without waiting on a community dataset to catch up. We use it for the
numbers a coach actually cites: cooldowns, mana costs, cast ranges, damage,
and talents.

Disk-cached per hero. Every call degrades to {} on failure — OpenDota
constants (see abilities.py) remain the fallback.
"""
from __future__ import annotations
import json
import re
import os

import requests

_DIR = os.path.join(os.path.dirname(__file__), "valve_cache")
_URL = "https://www.dota2.com/datafeed/herodata?language=english&hero_id={}"
_UA = {"User-Agent": "Mozilla/5.0 (DotaReplayCoach)"}

# Cast-range / cooldown live in special_values under these engine keys.
_RANGE_KEYS = ("AbilityCastRange", "cast_range", "radius", "AbilityCastRadius")
# Engine bookkeeping we never want to show a coach.
_SKIP_SPECIAL = {
    "AbilityCastPoint", "AbilityManaCost", "AbilityCooldown", "AbilityChargeRestoreTime",
    "AbilityCharges", "AbilityDuration", "LinkedSpecialBonus", "abilitycastpoint",
}


def _first(vals):
    if isinstance(vals, list) and vals:
        return vals[0]
    return vals or None


def _nonzero(seq):
    """Collapse [0,0,0] -> None, [70,60,50] -> [70,60,50], [700]*4 -> 700."""
    if not isinstance(seq, list) or not seq:
        return None
    if all((v or 0) == 0 for v in seq):
        return None
    uniq = list(dict.fromkeys(seq))
    return uniq[0] if len(uniq) == 1 else seq


_TAG_RE = re.compile(r"<[^>]+>")
_VAR_RE = re.compile(r"%([a-zA-Z_][a-zA-Z0-9_]*)%")


def _clean(text: str | None, values: dict) -> str:
    """Resolve Valve's %variable% templating and strip HTML.

    Left raw, a description reads '...%immunity_resist%%% magic resistance',
    which the model would happily echo into advice.
    """
    if not text:
        return ""
    out = _TAG_RE.sub(" ", text)

    def sub(m):
        v = values.get(m.group(1))
        if v is None:
            return ""
        if isinstance(v, list):
            return "/".join(str(int(x) if float(x).is_integer() else x) for x in v)
        return str(int(v) if float(v).is_integer() else v)

    out = _VAR_RE.sub(sub, out)
    out = out.replace("%%", "%").replace(" %", " ")
    return " ".join(out.split())


def _ability_profile(a: dict) -> dict:
    """Compact, coach-relevant numbers for one ability."""
    out = {"name": a.get("name_loc") or a.get("name")}
    cd = _nonzero(a.get("cooldowns"))
    mana = _nonzero(a.get("mana_costs"))
    dmg = _nonzero(a.get("damages"))
    if cd:   out["cooldown"] = cd
    if mana: out["mana"] = mana
    if dmg:  out["damage"] = dmg

    rng, extras, all_vals = None, {}, {}
    for s in (a.get("special_values") or []):
        name = s.get("name") or ""
        vals = _nonzero(s.get("values_float"))
        if vals is None:
            continue
        all_vals[name] = vals
        if name in _RANGE_KEYS and rng is None:
            rng = vals
        elif name not in _SKIP_SPECIAL and len(extras) < 4:
            extras[name] = vals
    if rng:
        out["cast_range"] = rng
    if extras:
        out["values"] = extras

    if (desc := _clean(a.get("desc_loc"), all_vals)):
        out["desc"] = desc[:200]
    for key, field in (("shard", "shard_loc"), ("scepter", "scepter_loc")):
        if (txt := _clean(a.get(field), all_vals)):
            out[key] = txt[:160]
    if a.get("ability_is_innate"):
        out["innate"] = True
    return out


_FLAGS_CACHE = os.path.join(os.path.dirname(__file__), "ability_flags.json")


def _interaction_flags() -> dict:
    """key -> {pierces_bkb, dispellable, damage_type} from OpenDota constants.

    These settle the claims a coach is most likely to get wrong: whether BKB
    actually stops an ability, and whether it can be dispelled. Berserker's
    Call, Dismember and Duel all PIERCE spell immunity — advice that says
    otherwise is simply false, so the model must read it rather than infer it.
    """
    if os.path.exists(_FLAGS_CACHE):
        try:
            return json.load(open(_FLAGS_CACHE, encoding="utf-8"))
        except Exception:
            pass
    try:
        raw = requests.get("https://api.opendota.com/api/constants/abilities",
                           timeout=40).json()
    except Exception:
        return {}
    out = {}
    for key, a in raw.items():
        if not isinstance(a, dict):
            continue
        pierce, disp = a.get("bkbpierce"), a.get("dispellable")
        if pierce is None and disp is None and not a.get("dmg_type"):
            continue
        out[key] = {"pierces_bkb": pierce, "dispellable": disp,
                    "damage_type": a.get("dmg_type")}
    try:
        json.dump(out, open(_FLAGS_CACHE, "w", encoding="utf-8"))
    except Exception:
        pass
    return out


def hero_profile(hero_id: int) -> dict:
    """{name, abilities:[...], talents:[...]} for one hero. {} on failure."""
    os.makedirs(_DIR, exist_ok=True)
    cache = os.path.join(_DIR, f"hero_{hero_id}.json")
    if os.path.exists(cache):
        try:
            return json.load(open(cache, encoding="utf-8"))
        except Exception:
            pass
    try:
        r = requests.get(_URL.format(int(hero_id)), timeout=20, headers=_UA)
        r.raise_for_status()
        heroes = (r.json().get("result", {}).get("data", {}).get("heroes") or [])
        if not heroes:
            return {}
        h = heroes[0]
    except Exception:
        return {}

    flags = _interaction_flags()
    abilities = []
    for a in (h.get("abilities") or []):
        name = a.get("name") or ""
        if name.startswith("special_bonus") or a.get("type") == 2:
            continue                      # talents live under their own key
        prof = _ability_profile(a)
        if not prof.get("name"):
            continue
        # Kit shape. Without these, a scepter-granted ability (Spinner's Snare)
        # and a passive innate (Spider's Milk) look like normal skills the hero
        # always had — which is how they ended up on the cooldown strip of a
        # player who never bought Aghanim's.
        prof["abilityId"] = a.get("id")
        prof["is_ult"] = a.get("type") == 1
        prof["innate"] = bool(a.get("ability_is_innate"))
        prof["needs_scepter"] = bool(a.get("ability_is_granted_by_scepter"))
        prof["needs_shard"] = bool(a.get("ability_is_granted_by_shard"))
        # DOTA_ABILITY_BEHAVIOR_PASSIVE = 2. A passive ult (Tiny's Grow) is not
        # a threat that can be "used on you", and has no cooldown to track.
        # Valve sends this as a string once the bitmask exceeds 32 bits.
        try:
            behavior = int(a.get("behavior") or 0)
        except (TypeError, ValueError):
            behavior = 0
        prof["passive"] = bool(behavior & 2)
        fl = flags.get(name) or {}
        for k in ("pierces_bkb", "dispellable", "damage_type"):
            if fl.get(k) is not None:
                prof[k] = fl[k]
        abilities.append(prof)

    # Talents are a separate top-level array, and their labels carry {s:key}
    # placeholders. Most resolve from the *linked ability's* special_values,
    # which publish a `bonuses` list keyed by the talent's internal name.
    bonus_by_talent: dict[str, dict[str, float]] = {}
    for a in (h.get("abilities") or []):
        for s in (a.get("special_values") or []):
            for b in (s.get("bonuses") or []):
                if b.get("name") is not None and b.get("value") is not None:
                    bonus_by_talent.setdefault(b["name"], {})[s.get("name")] = b["value"]

    def _fmt(v):
        return int(v) if float(v).is_integer() else v

    talents = []
    for t in (h.get("talents") or []):
        label, tname = t.get("name_loc") or "", t.get("name") or ""
        if not label:
            continue
        for s in (t.get("special_values") or []):       # self-contained talents
            val = _first(s.get("values_float"))
            if val is not None:
                label = label.replace("{s:%s}" % s.get("name"), str(_fmt(val)))
        for key, val in (bonus_by_talent.get(tname) or {}).items():
            # Labels reference the linked special as {s:bonus_<name>}, while the
            # ability publishes it as plain <name> — accept either spelling.
            label = label.replace("{s:%s}" % key, str(_fmt(val)))
            label = label.replace("{s:bonus_%s}" % key, str(_fmt(val)))
        label = " ".join(label.split())
        if "{s:" not in label:                          # drop anything unresolved
            talents.append(label)

    out = {
        "name": h.get("name_loc"),
        "abilities": abilities,
        "talents": talents[:8],
        "facets": [f.get("title_loc") for f in (h.get("facets") or []) if f.get("title_loc")],
    }
    try:
        json.dump(out, open(cache, "w", encoding="utf-8"))
    except Exception:
        pass
    return out
