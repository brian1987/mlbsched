"""TV broadcast partners per game — pulls ?hydrate=broadcasts from MLB schedule API."""

import io
import re
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

import mlbsched as sched
from mlbsched import (
    BOLD, DIM, RESET, RED, GREEN, YELLOW, CYAN, WHITE, GRAY,
    TEAMS, MLB_API, abv_from_id, fmt_team, fmt_game_time, today_et,
)

# Strip sponsor suffixes like " Presented by Progressive" so display stays tight.
_SPONSOR_RE = re.compile(r"\s+Presented by .*$", re.IGNORECASE)


def _clean_name(name: str) -> str:
    return _SPONSOR_RE.sub("", name).strip()


def _fetch_with_broadcasts(date_str: str, team_id: int | None = None) -> dict:
    params = {
        "sportId": 1,
        "date": date_str,
        "hydrate": "broadcasts,team",
    }
    if team_id:
        params["teamId"] = team_id
    resp = requests.get(f"{MLB_API}/schedule", params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()


def _tv_english(broadcasts: list[dict]) -> list[dict]:
    return [b for b in broadcasts if b.get("type") == "TV" and b.get("language") == "en"]


def _split_feeds(tv_broadcasts: list[dict]) -> tuple[list[dict], dict | None, dict | None]:
    """Return (national_feeds, away_feed, home_feed). National feeds dedup by cleaned name."""
    seen: set[str] = set()
    nationals: list[dict] = []
    for b in tv_broadcasts:
        if not b.get("isNational"):
            continue
        key = _clean_name(b.get("name", ""))
        if key in seen:
            continue
        seen.add(key)
        nationals.append(b)
    away = next((b for b in tv_broadcasts if not b.get("isNational") and b.get("homeAway") == "away"), None)
    home = next((b for b in tv_broadcasts if not b.get("isNational") and b.get("homeAway") == "home"), None)
    return nationals, away, home


# ── Renderer ──────────────────────────────────────────────────────────────────

def render_broadcasts(team_abv: str | None = None, out=None, tz: ZoneInfo | None = None) -> str:
    buf = io.StringIO()
    _out = out or buf

    def p(s=""):
        print(s, file=_out)

    team_id = None
    if team_abv:
        abv = team_abv.upper()
        if abv not in TEAMS:
            p()
            p(f"  {RED}Unknown team: {abv}{RESET}")
            p(f"  {GRAY}Try: curl mlbsched.run/teams{RESET}")
            p()
            return buf.getvalue()
        team_id = TEAMS[abv][0]

    today = today_et()
    date_str = today.strftime("%Y-%m-%d")
    label = today.strftime("%A, %B %-d, %Y")

    if team_abv:
        abv = team_abv.upper()
        color = sched.team_color(abv)
        title = f"{BOLD}{color}{TEAMS[abv][1]}{RESET} {GRAY}broadcasts{RESET} — {BOLD}{WHITE}{label}{RESET}"
    else:
        title = f"{BOLD}{CYAN}MLB Broadcasts{RESET} — {BOLD}{WHITE}{label}{RESET}"

    p()
    p(f"  {title}")
    p(f"  {GRAY}{'─' * 60}{RESET}")

    try:
        data = _fetch_with_broadcasts(date_str, team_id)
    except requests.RequestException:
        p(f"  {YELLOW}Could not reach MLB API.{RESET}")
        p()
        return buf.getvalue()

    games = []
    for date_block in data.get("dates", []):
        games.extend(date_block.get("games", []))

    if not games:
        p(f"  {GRAY}No games scheduled.{RESET}")
        p()
        return buf.getvalue()

    for game in games:
        away_id = game["teams"]["away"]["team"]["id"]
        home_id = game["teams"]["home"]["team"]["id"]
        away_abv = abv_from_id(away_id)
        home_abv = abv_from_id(home_id)
        game_time = fmt_game_time(game.get("gameDate", ""), tz)

        header = f"  {fmt_team(away_abv)} {DIM}@{RESET} {fmt_team(home_abv)}"
        if game_time:
            header += f"   {CYAN}{game_time}{RESET}"
        p(header)

        tv = _tv_english(game.get("broadcasts", []))
        nationals, away, home = _split_feeds(tv)

        if not tv:
            p(f"    {GRAY}TBD{RESET}")
            p()
            continue

        for n in nationals:
            p(f"    {GREEN}Nat:{RESET} {_clean_name(n['name'])}")

        parts = []
        if home:
            parts.append(f"{_clean_name(home['name'])} {GRAY}({home_abv}){RESET}")
        if away:
            parts.append(f"{_clean_name(away['name'])} {GRAY}({away_abv}){RESET}")
        if parts:
            p(f"    {'  /  '.join(parts)}")
        elif not nationals:
            p(f"    {GRAY}TBD{RESET}")

        p()

    p(f"  {GRAY}TV broadcasts (English) only. Source: MLB Stats API.{RESET}")
    p()
    return buf.getvalue()


# ── JSON API ──────────────────────────────────────────────────────────────────

def _broadcast_json(b: dict) -> dict:
    return {
        "name":        _clean_name(b.get("name", "")),
        "call_sign":   b.get("callSign"),
        "type":        b.get("type"),
        "language":    b.get("language"),
        "home_away":   b.get("homeAway"),
        "is_national": bool(b.get("isNational")),
    }


def build_broadcasts_json(team_abv: str | None = None, tz: ZoneInfo | None = None) -> dict:
    team_id = None
    if team_abv:
        abv = team_abv.upper()
        if abv not in TEAMS:
            return {"error": f"Unknown team: {abv}"}
        team_id = TEAMS[abv][0]

    today = today_et()
    try:
        data = _fetch_with_broadcasts(today.strftime("%Y-%m-%d"), team_id)
    except requests.RequestException as e:
        return {"error": f"upstream: {e}"}

    games_out = []
    for date_block in data.get("dates", []):
        for game in date_block.get("games", []):
            away_id = game["teams"]["away"]["team"]["id"]
            home_id = game["teams"]["home"]["team"]["id"]
            tv = _tv_english(game.get("broadcasts", []))
            nationals, away, home = _split_feeds(tv)
            games_out.append({
                "away":      abv_from_id(away_id),
                "home":      abv_from_id(home_id),
                "game_time": fmt_game_time(game.get("gameDate", ""), tz) or None,
                "national":  [_broadcast_json(n) for n in nationals],
                "away_feed": _broadcast_json(away) if away else None,
                "home_feed": _broadcast_json(home) if home else None,
            })

    out: dict = {"date": today.isoformat(), "games": games_out}
    if team_abv:
        out["team"] = team_abv.upper()
    return out
