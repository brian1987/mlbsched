"""h2h — head-to-head season series between two teams"""

import io
from datetime import datetime, timezone as _UTC
from zoneinfo import ZoneInfo

import requests

from mlbsched import (
    BOLD, DIM, RESET, RED, GREEN, CYAN, WHITE, GRAY,
    TEAMS, MLB_API, ET, abv_from_id, team_color, today_et, fmt_game_time,
)


def fetch_h2h(team_a_id: int, team_b_id: int, season: int) -> list[dict]:
    params = {
        "sportId":    1,
        "teamId":     team_a_id,
        "opponentId": team_b_id,
        "startDate":  f"{season}-03-01",
        "endDate":    f"{season}-11-01",
        "gameType":   "R",
        "hydrate":    "team,linescore",
    }
    try:
        resp = requests.get(f"{MLB_API}/schedule", params=params, timeout=10)
        resp.raise_for_status()
    except requests.RequestException:
        return []
    data = resp.json()
    games: list[dict] = []
    for date_block in data.get("dates", []):
        for g in date_block.get("games", []):
            games.append(g)
    games.sort(key=lambda g: g.get("gameDate", ""))
    return games


def _is_no_play(g: dict) -> bool:
    s = (g["status"].get("detailedState") or "").lower()
    return "postponed" in s or "cancel" in s or "suspended" in s


def summarize(games: list[dict], abv_a: str, abv_b: str) -> dict:
    a_wins = b_wins = 0
    a_runs = b_runs = 0
    completed: list[dict] = []
    upcoming:  list[dict] = []
    for g in games:
        abstract   = g["status"]["abstractGameState"]
        away_id    = g["teams"]["away"]["team"]["id"]
        home_id    = g["teams"]["home"]["team"]["id"]
        away_abv   = abv_from_id(away_id)
        away_score = g["teams"]["away"].get("score")
        home_score = g["teams"]["home"].get("score")

        if abstract == "Final" and away_score is not None and home_score is not None:
            completed.append(g)
            if away_abv == abv_a:
                a_runs += away_score
                b_runs += home_score
                if away_score > home_score:
                    a_wins += 1
                else:
                    b_wins += 1
            else:
                a_runs += home_score
                b_runs += away_score
                if home_score > away_score:
                    a_wins += 1
                else:
                    b_wins += 1
        elif not _is_no_play(g):
            upcoming.append(g)

    return {
        "a_wins":    a_wins,
        "b_wins":    b_wins,
        "a_runs":    a_runs,
        "b_runs":    b_runs,
        "completed": completed,
        "upcoming":  upcoming,
    }


def _fmt_date(gt_str: str, tz: ZoneInfo | None = None) -> str:
    try:
        dt_utc = datetime.strptime(gt_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_UTC.utc)
        return dt_utc.astimezone(tz or ET).strftime("%b %-d")
    except Exception:
        return ""


def render_h2h(abv_a: str, abv_b: str, tz: ZoneInfo | None = None, out=None) -> str:
    buf = io.StringIO()
    _out = out or buf

    def p(s=""):
        print(s, file=_out)

    abv_a = abv_a.upper()
    abv_b = abv_b.upper()
    if abv_a not in TEAMS or abv_b not in TEAMS:
        bad = abv_a if abv_a not in TEAMS else abv_b
        p(f"\n  {RED}Unknown team: {bad}{RESET}")
        p(f"  {GRAY}Try: curl mlbsched.run/teams{RESET}\n")
        return buf.getvalue()
    if abv_a == abv_b:
        p(f"\n  {RED}Pick two different teams.{RESET}\n")
        return buf.getvalue()

    season    = today_et().year
    team_a_id = TEAMS[abv_a][0]
    team_b_id = TEAMS[abv_b][0]
    games     = fetch_h2h(team_a_id, team_b_id, season)
    s         = summarize(games, abv_a, abv_b)

    color_a = team_color(abv_a)
    color_b = team_color(abv_b)
    name_a  = TEAMS[abv_a][1]
    name_b  = TEAMS[abv_b][1]

    p()
    p(f"  {BOLD}{CYAN}Head-to-Head{RESET}  "
      f"{BOLD}{color_a}{name_a}{RESET} {GRAY}vs{RESET} "
      f"{BOLD}{color_b}{name_b}{RESET}  {GRAY}({season}){RESET}")
    p(f"  {GRAY}{'─' * 60}{RESET}")

    if not games:
        p(f"  {GRAY}No matchups scheduled this season.{RESET}\n")
        return buf.getvalue()

    a_wins, b_wins = s["a_wins"], s["b_wins"]
    a_runs, b_runs = s["a_runs"], s["b_runs"]

    if a_wins > b_wins:
        series = f"{BOLD}{GREEN}{abv_a} {a_wins}{RESET}{GRAY}-{RESET}{abv_b} {b_wins}"
    elif b_wins > a_wins:
        series = f"{abv_a} {a_wins}{GRAY}-{RESET}{BOLD}{GREEN}{abv_b} {b_wins}{RESET}"
    else:
        series = f"{abv_a} {a_wins}{GRAY}-{RESET}{abv_b} {b_wins}"

    p(f"  Series: {series}    {GRAY}Runs: {abv_a} {a_runs}  {abv_b} {b_runs}{RESET}")

    if s["completed"]:
        p(f"\n  {BOLD}{WHITE}Completed{RESET}")
        for g in s["completed"]:
            _print_completed(g, tz, _out)

    if s["upcoming"]:
        p(f"\n  {BOLD}{WHITE}Upcoming{RESET}")
        for g in s["upcoming"]:
            _print_upcoming(g, tz, _out)

    p()
    return buf.getvalue()


def _print_completed(g: dict, tz: ZoneInfo | None, out) -> None:
    away_abv   = abv_from_id(g["teams"]["away"]["team"]["id"])
    home_abv   = abv_from_id(g["teams"]["home"]["team"]["id"])
    away_score = g["teams"]["away"].get("score") or 0
    home_score = g["teams"]["home"].get("score") or 0
    date_str   = _fmt_date(g.get("gameDate", ""), tz)

    color_aw = team_color(away_abv)
    color_hm = team_color(home_abv)
    aw_won   = away_score > home_score

    aw_score = f"{BOLD}{WHITE}{away_score:>2}{RESET}" if aw_won else f"{GRAY}{away_score:>2}{RESET}"
    hm_score = f"{GRAY}{home_score:>2}{RESET}" if aw_won else f"{BOLD}{WHITE}{home_score:>2}{RESET}"

    print(
        f"    {GRAY}{date_str:<7}{RESET}  "
        f"{BOLD}{color_aw}{away_abv}{RESET} {aw_score}  "
        f"{DIM}@{RESET}  "
        f"{BOLD}{color_hm}{home_abv}{RESET} {hm_score}",
        file=out,
    )


def _print_upcoming(g: dict, tz: ZoneInfo | None, out) -> None:
    away_abv = abv_from_id(g["teams"]["away"]["team"]["id"])
    home_abv = abv_from_id(g["teams"]["home"]["team"]["id"])
    date_str = _fmt_date(g.get("gameDate", ""), tz)
    time_str = fmt_game_time(g.get("gameDate", ""), tz)

    color_aw = team_color(away_abv)
    color_hm = team_color(home_abv)
    print(
        f"    {GRAY}{date_str:<7}{RESET}  "
        f"{BOLD}{color_aw}{away_abv}{RESET}     "
        f"{DIM}@{RESET}  "
        f"{BOLD}{color_hm}{home_abv}{RESET}     "
        f"{CYAN}{time_str}{RESET}",
        file=out,
    )


def build_h2h_json(abv_a: str, abv_b: str) -> dict:
    season    = today_et().year
    team_a_id = TEAMS[abv_a][0]
    team_b_id = TEAMS[abv_b][0]
    games     = fetch_h2h(team_a_id, team_b_id, season)
    s         = summarize(games, abv_a, abv_b)

    def gj(g: dict) -> dict:
        return {
            "date":       g.get("gameDate", "")[:10],
            "away":       abv_from_id(g["teams"]["away"]["team"]["id"]),
            "home":       abv_from_id(g["teams"]["home"]["team"]["id"]),
            "away_score": g["teams"]["away"].get("score"),
            "home_score": g["teams"]["home"].get("score"),
            "status":     g["status"].get("detailedState"),
            "game_time":  g.get("gameDate"),
        }

    return {
        "season":    season,
        "team_a":    abv_a,
        "team_b":    abv_b,
        "a_wins":    s["a_wins"],
        "b_wins":    s["b_wins"],
        "a_runs":    s["a_runs"],
        "b_runs":    s["b_runs"],
        "completed": [gj(g) for g in s["completed"]],
        "upcoming":  [gj(g) for g in s["upcoming"]],
    }
