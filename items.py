"""Item id / internal-name lookups from OpenDota constants, cached to disk."""
from __future__ import annotations
import json
import os
import requests

_CACHE = os.path.join(os.path.dirname(__file__), "items.json")
_KEY_CACHE = os.path.join(os.path.dirname(__file__), "item_keys.json")
_CDN = "https://cdn.cloudflare.steamstatic.com"

# Junk / consumables we usually skip in death inventory snapshots.
_SKIP_KEYS = {
    "tango", "clarity", "flask", "enchanted_mango", "faerie_fire",
    "ward_observer", "ward_sentry", "tome_of_knowledge", "smoke_of_deceit",
    "dust", "blood_grenade", "great_famango", "famango", "block_of_cheese",
    "tpscroll",  # still tracked separately for TP detection
    "branches", "gauntlets", "slippers", "mantle", "circlet", "belt_of_strength",
    "boots_of_elves", "robe", "gloves", "blight_stone", "blades_of_attack",
    "chainmail", "helm_of_iron_will", "broadsword", "quarterstaff", "claymore",
    "ring_of_protection", "stout_shield", "quelling_blade", "infused_raindrop",
    "orb_of_venom", "orb_of_corrosion", "wind_lace", "ring_of_regen",
    "sobi_mask", "recipe",
}

BLINK_KEYS = {"blink", "arcane_blink", "swift_blink", "overwhelming_blink"}
FORCE_KEYS = {"force_staff", "hurricane_pike"}
TP_ITEM_KEYS = {"tpscroll", "travel_boots", "travel_boots_2"}


def load() -> dict:
    """{str(item_id): {'name': display name, 'icon': url, 'key': internal name}}."""
    if os.path.exists(_CACHE):
        try:
            data = json.load(open(_CACHE, encoding="utf-8"))
            # Older caches may lack 'key' — still usable for id lookup.
            if data:
                return data
        except Exception:
            pass
    return _refresh()


def load_by_key() -> dict:
    """{internal_name: {'id': int, 'name': str, 'icon': url}}."""
    if os.path.exists(_KEY_CACHE):
        try:
            data = json.load(open(_KEY_CACHE, encoding="utf-8"))
            if data:
                return data
        except Exception:
            pass
    _refresh()
    if os.path.exists(_KEY_CACHE):
        return json.load(open(_KEY_CACHE, encoding="utf-8"))
    return {}


def _refresh() -> dict:
    try:
        ids = requests.get("https://api.opendota.com/api/constants/item_ids", timeout=30).json()
        info = requests.get("https://api.opendota.com/api/constants/items", timeout=30).json()
        by_id = {}
        by_key = {}
        for iid, iname in ids.items():
            row = info.get(iname) or {}
            # Prefer Steam CDN; also keep a stable key-based fallback URL.
            icon = (_CDN + row["img"]) if row.get("img") else (
                f"{_CDN}/apps/dota2/images/dota_react/items/{iname}.png"
            )
            name = row.get("dname") or iname.replace("_", " ").title()
            comps = [c for c in (row.get("components") or []) if isinstance(c, str)]
            try:
                cost = int(row.get("cost") or 0)
            except Exception:
                cost = 0
            by_id[str(iid)] = {
                "name": name, "icon": icon, "key": iname,
                "components": comps, "cost": cost,
            }
            by_key[iname] = {
                "id": int(iid), "name": name, "icon": icon,
                "components": comps, "cost": cost,
            }
        json.dump(by_id, open(_CACHE, "w", encoding="utf-8"))
        json.dump(by_key, open(_KEY_CACHE, "w", encoding="utf-8"))
        return by_id
    except Exception as e:
        print(f"  (item data unavailable: {e})")
        return {}


def resolve_key(key: str, by_key: dict | None = None) -> dict | None:
    by_key = by_key if by_key is not None else load_by_key()
    return by_key.get(key)


def owned_keys_at(purchase_log: list, t: float) -> list[str]:
    """Internal item names purchased at or before t (order preserved, deduped)."""
    out: list[str] = []
    seen: set[str] = set()
    for ev in purchase_log or []:
        if ev.get("time") is None or ev["time"] > t:
            continue
        k = ev.get("key") or ""
        if not k or k.startswith("recipe_"):
            continue
        if k not in seen:
            seen.add(k)
            out.append(k)
        else:
            # re-buy / upgrade path — move to end as most recent
            out = [x for x in out if x != k]
            out.append(k)
    return out


def _component_parents(by_key: dict) -> dict[str, set[str]]:
    """child_key -> set of parent item keys that list it as a component."""
    parents: dict[str, set[str]] = {}
    for parent, meta in (by_key or {}).items():
        for child in meta.get("components") or []:
            parents.setdefault(child, set()).add(parent)
    # Common boot upgrades share the same base even when components lists vary.
    for boot in ("phase_boots", "power_treads", "arcane_boots", "tranquil_boots",
                 "travel_boots", "travel_boots_2", "boots"):
        parents.setdefault("boots", set()).add(boot)
        parents.setdefault("boots_of_speed", set()).add(boot)
    parents.setdefault("magic_stick", set()).add("magic_wand")
    return parents


def collapse_components(keys: list[str], by_key: dict | None = None) -> list[str]:
    """Drop ingredients once their upgraded parent is also owned."""
    by_key = by_key if by_key is not None else load_by_key()
    owned = set(keys)
    parents = _component_parents(by_key)
    hide: set[str] = set()
    for k in keys:
        for parent in parents.get(k, ()):
            if parent in owned:
                hide.add(k)
                break
    return [k for k in keys if k not in hide]


def _row_for_key(k: str, by_key: dict) -> dict:
    meta = by_key.get(k) or {}
    icon = meta.get("icon") or f"{_CDN}/apps/dota2/images/dota_react/items/{k}.png"
    return {
        "key": k,
        "name": meta.get("name") or k.replace("_", " ").title(),
        "icon": icon,
        "cost": meta.get("cost") or 0,
    }


def snapshot_items(purchase_log: list, t: float, limit: int = 6) -> list[dict]:
    """Dotabuff-style completed inventory estimate at time t from purchase_log."""
    by_key = load_by_key()
    keys = [k for k in owned_keys_at(purchase_log, t)
            if k not in _SKIP_KEYS and not k.startswith("recipe_")]
    keys = collapse_components(keys, by_key)
    # Prefer valuable / recently bought completed items (like Dotabuff builds).
    scored = []
    for i, k in enumerate(keys):
        meta = by_key.get(k) or {}
        cost = meta.get("cost") or 0
        scored.append((cost, i, k))
    scored.sort(key=lambda r: (r[0], r[1]))
    # Keep the most expensive recent items, but preserve purchase order among them.
    keep = {k for _, _, k in scored[-limit:]}
    keys = [k for k in keys if k in keep][-limit:]
    return [_row_for_key(k, by_key) for k in keys]


def from_item_ids(ids: list[int], item_db: dict | None = None,
                  limit: int = 6) -> list[dict]:
    """Build display rows from numeric item ids (e.g. OpenDota end inventory)."""
    item_db = item_db if item_db is not None else load()
    by_key = load_by_key()
    keys = []
    for iid in ids or []:
        if not iid:
            continue
        meta = item_db.get(str(iid)) or {}
        k = meta.get("key")
        if not k or k in _SKIP_KEYS or k.startswith("recipe_"):
            continue
        keys.append(k)
    keys = collapse_components(keys, by_key)[-limit:]
    return [_row_for_key(k, by_key) for k in keys]


def has_any(purchase_log: list, t: float, keys: set[str]) -> bool:
    return any((ev.get("key") in keys) and (ev.get("time") is not None)
               and ev["time"] <= t for ev in (purchase_log or []))
