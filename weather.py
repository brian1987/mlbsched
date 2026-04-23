"""Stadium weather — wttr.in client with SQLite cache."""

import io
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone as _UTC

import requests

import db
from mlbsched import (
    BOLD, DIM, RESET, RED, GREEN, YELLOW, BLUE, CYAN, WHITE, GRAY,
    TEAMS, STADIUMS, SPECIAL_VENUES, team_color, fmt_team, today_et, ET,
    fmt_game_time, abv_from_id,
)

CACHE_TTL_SECONDS = 1800  # 30 minutes

# Stadiums where weather is not meaningful (fixed dome, no retractable roof).
# Retractable-roof parks still get weather since roofs are often open.
INDOOR_STADIUMS: set[str] = set()  # currently empty — Rogers Centre, etc. are retractable


def _cache_key(lat: float, lon: float) -> str:
    return f"{round(lat, 3)},{round(lon, 3)}"


def _age_seconds(fetched_at_iso: str) -> float:
    dt = datetime.fromisoformat(fetched_at_iso)
    return (datetime.now(_UTC.utc) - dt).total_seconds()


def get_weather(lat: float, lon: float) -> dict | None:
    """Fetch current weather from wttr.in. Returns normalized dict or None on failure."""
    key = _cache_key(lat, lon)
    cached = db.read_weather_cache(key)
    if cached is not None and _age_seconds(cached["fetched_at"]) < CACHE_TTL_SECONDS:
        try:
            return json.loads(cached["data"])
        except (ValueError, TypeError):
            pass

    try:
        resp = requests.get(
            f"https://wttr.in/{lat},{lon}",
            params={"format": "j1"},
            headers={"User-Agent": "mlbsched.run"},
            timeout=6,
        )
        resp.raise_for_status()
        data = resp.json()
    except (requests.RequestException, ValueError):
        if cached is not None:
            try:
                return json.loads(cached["data"])
            except (ValueError, TypeError):
                return None
        return None

    current = (data.get("current_condition") or [{}])[0]
    try:
        normalized = {
            "temp_f":    int(current.get("temp_F")) if current.get("temp_F") else None,
            "wind_mph":  int(current.get("windspeedMiles")) if current.get("windspeedMiles") else None,
            "wind_dir":  current.get("winddir16Point"),
            "condition": (current.get("weatherDesc") or [{}])[0].get("value"),
            "humidity":  int(current.get("humidity")) if current.get("humidity") else None,
        }
    except (TypeError, ValueError):
        return None

    db.write_weather_cache(key, json.dumps(normalized))
    return normalized


def fmt_weather_compact(w: dict | None) -> str | None:
    if not w:
        return None
    parts: list[str] = []
    if w.get("temp_f") is not None:
        parts.append(f"{w['temp_f']}°F")
    if w.get("wind_dir") and w.get("wind_mph") is not None:
        parts.append(f"wind {w['wind_dir']} {w['wind_mph']}mph")
    elif w.get("wind_mph") is not None:
        parts.append(f"wind {w['wind_mph']}mph")
    if w.get("condition"):
        parts.append(w["condition"])
    return " / ".join(parts) if parts else None


def stadium_location(game: dict) -> tuple[str, float, float] | None:
    """Return (stadium_name, lat, lon) for a game, or None if unknown."""
    venue_name = game.get("venue", {}).get("name", "")
    if venue_name in SPECIAL_VENUES:
        return SPECIAL_VENUES[venue_name]
    home_id  = game["teams"]["home"]["team"]["id"]
    home_abv = abv_from_id(home_id)
    return STADIUMS.get(home_abv)


def is_indoor(game: dict) -> bool:
    home_id  = game["teams"]["home"]["team"]["id"]
    home_abv = abv_from_id(home_id)
    return home_abv in INDOOR_STADIUMS


def weather_line_for_game(game: dict) -> str | None:
    """Returns the formatted weather string for a game, or None to skip."""
    if is_indoor(game):
        return None
    loc = stadium_location(game)
    if not loc:
        return None
    _, lat, lon = loc
    w = get_weather(lat, lon)
    return fmt_weather_compact(w)


# ── Renderer ──────────────────────────────────────────────────────────────────

def render_weather(out=None) -> str:
    buf = io.StringIO()
    _out = out or buf

    def p(s=""):
        print(s, file=_out)

    import mlbsched as sched
    today    = today_et()
    date_str = today.strftime("%Y-%m-%d")
    label    = today.strftime("%A, %B %-d, %Y")
    data     = sched.fetch_schedule(date_str)

    all_games = [
        game
        for date_block in data.get("dates", [])
        for game in date_block.get("games", [])
    ]

    p()
    p(f"  {BOLD}{CYAN}Stadium Weather{RESET} — {BOLD}{WHITE}{label}{RESET}")
    p(f"  {GRAY}{'─' * 60}{RESET}")

    if not all_games:
        p(f"  {GRAY}No games scheduled.{RESET}")
        p()
        return buf.getvalue()

    # Parallel prefetch so cold render doesn't serialize 15 wttr.in calls
    locs: list[tuple[float, float]] = []
    seen: set[str] = set()
    for game in all_games:
        if is_indoor(game):
            continue
        loc = stadium_location(game)
        if loc:
            _, lat, lon = loc
            key = _cache_key(lat, lon)
            if key not in seen:
                seen.add(key)
                locs.append((lat, lon))
    if locs:
        with ThreadPoolExecutor(max_workers=8) as ex:
            list(ex.map(lambda ll: get_weather(*ll), locs))

    for game in all_games:
        away_id  = game["teams"]["away"]["team"]["id"]
        home_id  = game["teams"]["home"]["team"]["id"]
        away_abv = abv_from_id(away_id)
        home_abv = abv_from_id(home_id)
        gt       = fmt_game_time(game.get("gameDate", ""))
        loc      = stadium_location(game)
        stadium  = loc[0] if loc else "Unknown"

        line = f"  {fmt_team(away_abv)} {DIM}@{RESET} {fmt_team(home_abv)}"
        if gt:
            line += f"   {CYAN}{gt}{RESET}"
        p(line)

        if is_indoor(game):
            p(f"    {GRAY}{stadium} — indoor{RESET}")
            continue

        wx = weather_line_for_game(game)
        if wx:
            p(f"    {GRAY}{stadium}  {RESET}{wx}")
        else:
            p(f"    {GRAY}{stadium} — weather unavailable{RESET}")

    p()
    p(f"  {GRAY}Weather via wttr.in — current conditions at each stadium.{RESET}")
    return buf.getvalue()
