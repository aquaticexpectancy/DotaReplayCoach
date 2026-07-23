"""Reusable report-generation pipeline, shared by the CLI (main.py) and the
web app (webapp.py). One code path so the two never drift.
"""
from __future__ import annotations
import os

import opendota
from stratz_client import fetch_match
from detect_deaths import analyze_match
from render_report import render

REPORTS_DIR = os.path.join(os.path.dirname(__file__), "reports")
STEAM64_BASE = 76561197960265728


class PipelineError(Exception):
    """A clean, user-facing failure (bad match, account not in game, etc.)."""


def to_steam32(value) -> int:
    """Accept either a 32-bit account id or a 64-bit SteamID; return 32-bit."""
    v = int(value)
    return v - STEAM64_BASE if v > STEAM64_BASE else v


def report_path(match_id: int, account_id: int) -> str:
    return os.path.join(REPORTS_DIR, f"match_{match_id}_{account_id}.html")


def _pick(match, account_id: int):
    for p in match.players:
        if p.account_id == account_id:
            return p
    return None


def generate(match_id, account_id, *, wait_parse: bool = True,
             parse_timeout: float = 240, force: bool = False,
             use_cache: bool = True, coach: bool = False, log=print) -> dict:
    """Build (or reuse) the report for one player in one match.

    Returns {path, match_id, account_id, hero_id, deaths, cached, partial}.
    Raises PipelineError for clean, explainable failures.
    """
    match_id = int(match_id)
    account_id = to_steam32(account_id)
    os.makedirs(REPORTS_DIR, exist_ok=True)
    path = report_path(match_id, account_id)

    if use_cache and os.path.exists(path) and not force:
        log("Using cached report.")
        return {"path": path, "match_id": match_id, "account_id": account_id,
                "hero_id": None, "deaths": None, "cached": True, "partial": False}

    log(f"STRATZ: fetching match {match_id}…")
    try:
        match = fetch_match(match_id, log=log)
    except TimeoutError as e:
        raise PipelineError(str(e))
    if not match.parsed:
        raise PipelineError(
            "STRATZ has no parsed replay for this match yet. Very recent or very "
            "old matches may not be available — try again later.")

    partial = False
    od = {}
    log("OpenDota: wards, tower timeline, economy…")
    try:
        od = opendota.fetch(match_id, ensure=True, wait_parse=wait_parse,
                            parse_timeout=parse_timeout, force_parse=force)
        match.wards = opendota.wards(od)
        match.building_kills = opendota.building_kills(od)
        opendota.attach_economy(match, od)
        partial = not opendota.is_fully_parsed(od)
    except Exception as e:                       # OpenDota is best-effort
        log(f"OpenDota unavailable ({e}); continuing with partial data.")
        partial = True
    match.buildings = opendota.load_buildings()

    me = _pick(match, account_id)
    if me is None:
        raise PipelineError(
            f"Account {account_id} did not play in match {match_id}. "
            "Check the Steam ID and that it matches this match.")

    log(f"Analyzing {len(me.deaths)} deaths…")
    ranked = analyze_match(match, me)
    if not ranked:
        raise PipelineError("No deaths found for this player — nothing to review.")

    advice = None
    if coach:
        log("Asking Grok for targeted coaching…")
        import coach as coach_mod
        import diagnose
        diags = {}
        for a in ranked:
            dg = diagnose.movement(match, me, a)
            if dg:
                diags[a.index] = dg
        import heroes as _h
        hero = _h.load()
        habit = diagnose.habits(
            match, me, ranked, diags, __import__("items").load(),
            lambda h: (hero.get(str(h), {}).get("name", str(h)), None))
        import summary as summary_mod
        try:
            ms = summary_mod.build(od, match, me, ranked)
        except Exception as e:                       # summary is additive, never fatal
            log(f"  (match summary unavailable: {e})")
            ms = {}
        advice = coach_mod.advise(
            coach_mod.build_context(match, me, ranked, diags, habit, ms), log=log)

    render(match, me, ranked, path, coach_advice=advice)
    log("Report ready.")
    return {"path": path, "match_id": match_id, "account_id": account_id,
            "hero_id": me.hero_id, "deaths": len(ranked),
            "cached": False, "partial": partial}
