"""Ability id/name/cooldown lookups from OpenDota constants (cached)."""
from __future__ import annotations
import json
import os
import requests

_DIR = os.path.dirname(__file__)
_IDS_CACHE = os.path.join(_DIR, "ability_ids.json")
_META_CACHE = os.path.join(_DIR, "abilities_meta.json")
_HERO_ULT_CACHE = os.path.join(_DIR, "hero_ultimates.json")
_CDN = "https://cdn.cloudflare.steamstatic.com"

_SKIP_KEYS = (
    "generic_hidden", "special_bonus_", "plus_", "twin_gate", "ability_lamp",
    "ability_capture", "cny_", "seasonal_", "high_five", "spray_custom",
)


def _refresh() -> None:
    ids = requests.get("https://api.opendota.com/api/constants/ability_ids", timeout=60).json()
    abilities = requests.get("https://api.opendota.com/api/constants/abilities", timeout=60).json()
    hero_abs = requests.get("https://api.opendota.com/api/constants/hero_abilities", timeout=60).json()
    heroes = requests.get("https://api.opendota.com/api/heroStats", timeout=60).json()

    meta = {}
    for iid, key in ids.items():
        try:
            int(iid)
        except Exception:
            continue
        row = abilities.get(key) or {}
        cd = row.get("cd")
        cds = []
        values = cd if isinstance(cd, list) else ([] if cd is None or cd == "" else [cd])
        for x in values:
            try:
                if x is None or str(x).strip() == "":
                    continue
                cds.append(float(x))
            except Exception:
                pass
        icon = None
        if row.get("img"):
            icon = _CDN + row["img"]
        meta[str(iid)] = {
            "key": key,
            "name": row.get("dname") or key.replace("_", " ").title(),
            "cd": cds,
            "icon": icon,
        }
    json.dump({k: v for k, v in ids.items() if str(k).isdigit()},
              open(_IDS_CACHE, "w", encoding="utf-8"))
    json.dump(meta, open(_META_CACHE, "w", encoding="utf-8"))

    # Map hero_id -> ultimate ability id (OpenDota ability list order: ult is last real spell).
    key_to_id = {}
    for k, v in ids.items():
        try:
            key_to_id[v] = int(k)
        except Exception:
            continue
    npc_to_hid = {h.get("name"): h["id"] for h in heroes if h.get("name")}
    ults = {}
    for npc, row in hero_abs.items():
        hid = npc_to_hid.get(npc)
        if hid is None:
            continue
        abs_list = row.get("abilities") or []
        ult_key = None
        # OpenDota lists ultimates in slot index 5; later entries are often facets/innates.
        if len(abs_list) > 5 and isinstance(abs_list[5], str):
            cand = abs_list[5]
            if cand and not any(cand.startswith(p) or p in cand for p in _SKIP_KEYS):
                ult_key = cand
        if not ult_key:
            for k in abs_list:
                if not isinstance(k, str):
                    continue
                if not k or any(k.startswith(p) or p in k for p in _SKIP_KEYS):
                    continue
                ult_key = k
        if ult_key and ult_key in key_to_id:
            ults[str(hid)] = {
                "abilityId": key_to_id[ult_key],
                "key": ult_key,
                "name": (abilities.get(ult_key) or {}).get("dname") or ult_key,
            }
    json.dump(ults, open(_HERO_ULT_CACHE, "w", encoding="utf-8"))


def load_meta() -> dict:
    if not os.path.exists(_META_CACHE) or not os.path.exists(_HERO_ULT_CACHE):
        try:
            _refresh()
        except Exception as e:
            print(f"  (ability data unavailable: {e})")
            return {}
    try:
        return json.load(open(_META_CACHE, encoding="utf-8"))
    except Exception:
        return {}


def load_ultimates() -> dict:
    if not os.path.exists(_HERO_ULT_CACHE):
        load_meta()
    try:
        return json.load(open(_HERO_ULT_CACHE, encoding="utf-8"))
    except Exception:
        return {}


def info(ability_id: int, meta: dict | None = None) -> dict:
    meta = meta if meta is not None else load_meta()
    return meta.get(str(ability_id)) or {
        "key": f"ability_{ability_id}",
        "name": f"Ability {ability_id}",
        "cd": [],
        "icon": None,
    }


def is_noise(ability_id: int, meta: dict | None = None) -> bool:
    key = info(ability_id, meta).get("key") or ""
    return any(key.startswith(p) or p in key for p in _SKIP_KEYS)


def cooldown(ability_id: int, hero_level: int, meta: dict | None = None) -> float | None:
    """Estimate current cooldown from hero level (ult ranks at 6/12/18)."""
    cds = info(ability_id, meta).get("cd") or []
    if not cds:
        return None
    if len(cds) == 1:
        return cds[0]
    # Multi-rank: map hero level → skill rank roughly.
    if hero_level >= 18:
        rank = min(3, len(cds))
    elif hero_level >= 12:
        rank = min(2, len(cds))
    elif hero_level >= 6:
        rank = 1
    else:
        rank = 1
    return cds[rank - 1]


def ult_rank_available(hero_level: int) -> bool:
    return hero_level >= 6
