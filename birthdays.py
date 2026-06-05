"""birthdays — active MLB players born on today's calendar date.

v1 covers current-season (active) players, so ages are always live and the
single roster fetch keeps it fast. An all-time "legends born today" mode would
mean sweeping historical seasons (the /api/v1/sports/1/players?season= endpoint
serves them) — a natural future expansion if we want Ruth & Mays in the mix.
"""

import io
import time

import requests

from mlbsched import (
    BOLD, RESET, GRAY, GREEN, WHITE, DIM,
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
