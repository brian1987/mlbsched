"""birthdays — players born on today's calendar date.

Two modes:
- /birthdays      active (current-season) players, fetched live so ages are
                  always current and it's a single fast roster call.
- /birthdays/all  all-time "legends born today", served from a precomputed
                  static index (static/birthdays_alltime.json, built offline by
                  tools/build_birthdays_alltime.py). Sweeping ~150 seasons live
                  would be too slow for a request, so we bake it once.
"""

import io
import json
import time
from pathlib import Path

import requests

from mlbsched import (
    BOLD, RESET, GRAY, GREEN, WHITE, DIM, CYAN,
    MLB_API,
    TEAM_ID_TO_ABV,
    fmt_team,
    today_et,
)

# Current-season roster is ~1500 players and only changes day to day; cache it
# briefly so a burst of /birthdays hits doesn't re-fetch every time.
_roster_cache: tuple[float, list[dict]] | None = None
_ROSTER_TTL_SECONDS = 3600


def _season_players() -> list[dict]:
    global _roster_cache
    now = time.monotonic()
    if _roster_cache is not None and now - _roster_cache[0] < _ROSTER_TTL_SECONDS:
        return _roster_cache[1]
    try:
        resp = requests.get(
            f"{MLB_API}/sports/1/players",
            params={"season": today_et().year},
            timeout=10,
        )
        resp.raise_for_status()
        people = resp.json().get("people", [])
    except requests.RequestException:
        return _roster_cache[1] if _roster_cache is not None else []
    _roster_cache = (now, people)
    return people


def _born_today() -> list[dict]:
    """Active players whose birthday is today, sorted oldest-first (most senior)."""
    today = today_et()
    mmdd = f"{today.month:02d}-{today.day:02d}"
    hits = [p for p in _season_players() if (p.get("birthDate") or "")[5:] == mmdd]
    hits.sort(key=lambda p: p.get("birthDate", ""))   # earliest birth year first
    return hits


def _birthplace(p: dict) -> str:
    city = p.get("birthCity") or ""
    region = p.get("birthStateProvince") or p.get("birthCountry") or ""
    return ", ".join(part for part in (city, region) if part)


def _position(p: dict) -> str:
    pos = (p.get("primaryPosition") or {}).get("abbreviation") or "?"
    if pos == "P":
        hand = (p.get("pitchHand") or {}).get("code")
        return {"L": "LHP", "R": "RHP"}.get(hand, "P")
    return pos


def render_birthdays(out=None) -> str:
    buf = io.StringIO()
    _out = out or buf

    today = today_et()
    label = today.strftime("%B %-d")

    print(file=_out)
    print(f"  {BOLD}MLB birthdays — {label}{RESET}", file=_out)
    print(f"  {GRAY}{'─' * 52}{RESET}", file=_out)

    players = _born_today()
    if not players:
        print(f"  {GRAY}No active players were born on this date.{RESET}", file=_out)
        print(f"  {DIM}all-time legends born today: /birthdays/all{RESET}", file=_out)
        print(file=_out)
        return buf.getvalue()

    for p in players:
        age = today.year - int(p["birthDate"][:4])
        team_id = (p.get("currentTeam") or {}).get("id")
        abv = TEAM_ID_TO_ABV.get(team_id)
        # fmt_team pads to a 3-char visible column, keeping later columns aligned.
        team = fmt_team(abv) if abv else f"{GRAY}—{RESET}  "
        name = f"{BOLD}{WHITE}{p['fullName']}{RESET}"
        pos = _position(p)
        where = _birthplace(p)
        print(
            f"  {name:<32} {team}  {GRAY}{pos:<3}{RESET}  "
            f"{GREEN}turns {age}{RESET}  {DIM}{where}{RESET}",
            file=_out,
        )

    print(f"  {DIM}all-time legends born today: /birthdays/all{RESET}", file=_out)
    print(file=_out)
    return buf.getvalue()


def build_birthdays_json() -> dict:
    today = today_et()
    out: dict = {
        "kind":  "birthdays",
        "month": today.month,
        "day":   today.day,
        "players": [],
    }
    for p in _born_today():
        team_id = (p.get("currentTeam") or {}).get("id")
        out["players"].append({
            "name":       p.get("fullName"),
            "team":       TEAM_ID_TO_ABV.get(team_id),
            "position":   _position(p),
            "age":        today.year - int(p["birthDate"][:4]),
            "birth_date": p.get("birthDate"),
            "birthplace": _birthplace(p),
        })
    return out


# ── all-time "legends born today" ───────────────────────────────────────────────
# Served from a static index built offline (see module docstring). Loaded once
# and held for the life of the process — the file is shipped in the image.
_ALLTIME_PATH = Path(__file__).resolve().parent / "static" / "birthdays_alltime.json"
_alltime_cache: dict | None = None
# How many to print in the terminal view; the JSON API returns the full day.
_RENDER_LIMIT = 18


def _alltime_index() -> dict:
    global _alltime_cache
    if _alltime_cache is None:
        try:
            _alltime_cache = json.loads(_ALLTIME_PATH.read_text())
        except (OSError, ValueError):
            _alltime_cache = {"days": {}}
    return _alltime_cache


def _legends_today() -> list[dict]:
    today = today_et()
    mmdd = f"{today.month:02d}-{today.day:02d}"
    return _alltime_index().get("days", {}).get(mmdd, [])


def _span(e: dict) -> str:
    """e.g. '1914–1935' from debut/last dates."""
    debut = (e.get("debut") or "")[:4]
    last = (e.get("last") or "")[:4]
    if debut and last:
        return f"{debut}–{last}"
    return debut or last or "?"


def render_birthdays_alltime(out=None) -> str:
    buf = io.StringIO()
    _out = out or buf

    today = today_et()
    label = today.strftime("%B %-d")

    print(file=_out)
    print(f"  {BOLD}MLB legends born on {label}{RESET}", file=_out)
    print(f"  {GRAY}{'─' * 58}{RESET}", file=_out)

    players = _legends_today()
    if not players:
        print(f"  {GRAY}No players on record were born on this date.{RESET}", file=_out)
        print(file=_out)
        return buf.getvalue()

    for e in players[:_RENDER_LIMIT]:
        name = f"{BOLD}{WHITE}{e['name']}{RESET}"
        pos = e.get("pos") or "?"
        span = _span(e)
        seasons = e.get("seasons")
        seas = f"{seasons} yr" if seasons else ""
        # Nickname is the fun bit; fall back to birthplace when there isn't one.
        tail = f'"{e["nick"]}"' if e.get("nick") else _legend_birthplace(e)
        died = f"  {GRAY}d. {e['death'][:4]}{RESET}" if e.get("death") else ""
        print(
            f"  {name:<30} {GRAY}{pos:<4}{RESET} {GREEN}{span:<9}{RESET} "
            f"{CYAN}{seas:<6}{RESET} {DIM}{tail}{RESET}{died}",
            file=_out,
        )

    extra = len(players) - _RENDER_LIMIT
    if extra > 0:
        print(f"  {GRAY}… and {extra} more — see /api/birthdays/all{RESET}", file=_out)
    print(f"  {DIM}ranked by seasons in the majors · active players: /birthdays{RESET}", file=_out)
    print(file=_out)
    return buf.getvalue()


def _legend_birthplace(e: dict) -> str:
    return ", ".join(part for part in (e.get("city"), e.get("region")) if part)


def build_birthdays_alltime_json() -> dict:
    today = today_et()
    return {
        "kind":  "birthdays_alltime",
        "month": today.month,
        "day":   today.day,
        "generated": _alltime_index().get("generated"),
        "players": [
            {
                "name":       e.get("name"),
                "nickname":   e.get("nick"),
                "position":   e.get("pos"),
                "birth_date": e.get("birth"),
                "debut":      e.get("debut"),
                "last_played": e.get("last"),
                "death_date": e.get("death"),
                "seasons":    e.get("seasons"),
                "birthplace": _legend_birthplace(e),
            }
            for e in _legends_today()
        ],
    }
