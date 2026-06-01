"""wp — ASCII win probability sparklines for completed MLB games"""

import io
from datetime import datetime
from zoneinfo import ZoneInfo
import requests

from mlbsched import (
    BOLD, RESET, GRAY, WHITE, RED,
    TEAMS,
    MLB_API,
    abv_from_id,
    fetch_schedule,
    team_color,
)

BLOCKS = "▁▂▃▄▅▆▇█"  # 8 levels
CHART_WIDTH = 48


def fetch_wp(game_pk: int) -> list[dict]:
    """Return per-play WP entries from MLB's winProbability endpoint."""
    if not game_pk:
        return []
    try:
        resp = requests.get(f"{MLB_API}/game/{game_pk}/winProbability", timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _downsample(values: list[float], width: int) -> list[float]:
    n = len(values)
    if n == 0 or n <= width:
        return values
    out = []
    for i in range(width):
        start = (i * n) // width
        end = ((i + 1) * n) // width
        chunk = values[start:end] if end > start else [values[start]]
        out.append(sum(chunk) / len(chunk))
    return out


def _spark(values: list[float]) -> str:
    out = []
    for v in values:
        idx = min(7, max(0, int(v / 12.5)))
        out.append(BLOCKS[idx])
    return "".join(out)


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suf = "th"
    else:
        suf = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def find_biggest_swing(plays: list[dict]) -> dict | None:
    if not plays:
        return None
    best = max(plays, key=lambda p: abs(p.get("homeTeamWinProbabilityAdded") or 0))
    wpa = best.get("homeTeamWinProbabilityAdded") or 0
    if abs(wpa) < 0.1:
        return None
    about = best.get("about") or {}
    result = best.get("result") or {}
    desc = (result.get("description") or "").strip()
    if desc.endswith("."):
        desc = desc[:-1]
    return {
        "delta_home": wpa,
        "abs_delta": abs(wpa),
        "half": about.get("halfInning", ""),
        "inning": about.get("inning"),
        "description": desc,
    }


def render_wp_for_game(game: dict) -> str:
    """Sparkline + headline-moment line for one Final game. Empty otherwise."""
    status = (game.get("status") or {}).get("abstractGameState", "")
    if status != "Final":
        return ""
    plays = fetch_wp(game.get("gamePk"))
    if not plays:
        return ""

    home_wp = [p.get("homeTeamWinProbability", 50.0) for p in plays]
    spark = _spark(_downsample(home_wp, CHART_WIDTH))

    home_abv = abv_from_id(game["teams"]["home"]["team"]["id"])
    away_abv = abv_from_id(game["teams"]["away"]["team"]["id"])
    final_home_wp = home_wp[-1]

    winner_abv = home_abv if final_home_wp >= 50 else away_abv
    winner_color = team_color(winner_abv)

    buf = io.StringIO()
    print(file=buf)
    print(
        f"  {GRAY}WP {RESET}{winner_color}{spark}{RESET}  "
        f"{BOLD}{winner_color}{winner_abv}{RESET} {GRAY}→{RESET} {WHITE}100%{RESET}",
        file=buf,
    )

    swing = find_biggest_swing(plays)
    if swing and swing["inning"] is not None:
        delta = swing["delta_home"]
        benefit_abv = home_abv if delta > 0 else away_abv
        pct = int(round(abs(delta)))
        half_short = "bot" if swing["half"] == "bottom" else "top"
        inn_str = f"{half_short} {_ordinal(swing['inning'])}"
        print(
            f"  {GRAY}↳ biggest swing: {swing['description']}, "
            f"{inn_str}  (+{pct}% {benefit_abv}){RESET}",
            file=buf,
        )

    return buf.getvalue()


def render_wp(team_abv: str, date_str: str, tz: ZoneInfo | None = None, out=None) -> str:
    """Full /wp/<team>/<date> view — title, game lines, sparkline per Final."""
    import mlbsched as sched

    buf = io.StringIO()
    _out = out or buf

    def p(s=""):
        print(s, file=_out)

    abv = team_abv.upper()
    if abv not in TEAMS:
        p(f"{RED}Unknown team: {abv}{RESET}  —  try: curl mlbsched.run/teams")
        return buf.getvalue()

    team_id = TEAMS[abv][0]
    data = fetch_schedule(date_str, team_id)
    games = [g for blk in data.get("dates", []) for g in blk.get("games", [])]

    d = datetime.strptime(date_str, "%Y-%m-%d")
    label = d.strftime("%A, %B %-d, %Y")
    color = team_color(abv)

    p()
    p(f"  {BOLD}{color}{TEAMS[abv][1]}{RESET} — {BOLD}{WHITE}Win Probability{RESET}  {GRAY}{label}{RESET}")
    p(f"  {GRAY}{'─' * 52}{RESET}")

    if not games:
        p(f"  {GRAY}No games scheduled.{RESET}")
        p()
        return buf.getvalue()

    for game in games:
        sched._render_game_line(game, _out, tz=tz)
        status = (game.get("status") or {}).get("abstractGameState", "")
        if status == "Final":
            wp_block = render_wp_for_game(game)
            if wp_block:
                print(wp_block, file=_out, end="")
            else:
                p(f"  {GRAY}WP data unavailable.{RESET}")
        else:
            p(f"  {GRAY}WP shown only for completed games.{RESET}")
        p()

    return buf.getvalue()


def build_wp_json(team_abv: str, date_str: str) -> dict:
    abv = team_abv.upper()
    if abv not in TEAMS:
        return {"error": f"Unknown team: {abv}"}
    team_id = TEAMS[abv][0]
    data = fetch_schedule(date_str, team_id)
    games_out: list[dict] = []
    for blk in data.get("dates", []):
        for game in blk.get("games", []):
            status = (game.get("status") or {}).get("abstractGameState", "")
            game_pk = game.get("gamePk")
            entry = {
                "game_pk":       game_pk,
                "home":          abv_from_id(game["teams"]["home"]["team"]["id"]),
                "away":          abv_from_id(game["teams"]["away"]["team"]["id"]),
                "status":        status,
                "wp_home":       None,
                "biggest_swing": None,
            }
            if status == "Final":
                plays = fetch_wp(game_pk)
                if plays:
                    entry["wp_home"] = [round(p.get("homeTeamWinProbability", 50.0), 1) for p in plays]
                    entry["biggest_swing"] = find_biggest_swing(plays)
            games_out.append(entry)
    return {"team": abv, "date": date_str, "games": games_out}
