"""Hero id -> {name, icon_url}, fetched once from OpenDota and cached to disk.

Local HTML cards can load remote images (no artifact CSP), so we point <image>
tags straight at Valve's hero icon CDN.
"""
from __future__ import annotations
import json
import os
import requests

_CACHE = os.path.join(os.path.dirname(__file__), "heroes.json")
_CDN = "https://cdn.cloudflare.steamstatic.com"
_FALLBACK: dict = {}


def load() -> dict:
    """Return {str(hero_id): {'name': str, 'icon': url}}. Empty dict if offline."""
    if os.path.exists(_CACHE):
        try:
            return json.load(open(_CACHE, encoding="utf-8"))
        except Exception:
            pass
    try:
        rows = requests.get("https://api.opendota.com/api/heroStats", timeout=30).json()
        m = {str(h["id"]): {"name": h.get("localized_name", str(h["id"])),
                            "icon": _CDN + h["icon"]} for h in rows if h.get("icon")}
        json.dump(m, open(_CACHE, "w", encoding="utf-8"))
        return m
    except Exception as e:
        print(f"  (hero icons unavailable: {e})")
        return _FALLBACK
