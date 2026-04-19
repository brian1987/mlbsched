#!/usr/bin/env python3
"""mlbsched - MLB schedule in your terminal"""

import sys
import io
from datetime import date, datetime, timedelta
import requests

# ── ANSI colors ──────────────────────────────────────────────────────────────
RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"

RED    = "\033[91m"
GREEN  = "\033[92m"
YELLOW = "\033[93m"
BLUE   = "\033[94m"
CYAN   = "\033[96m"
WHITE  = "\033[97m"
GRAY   = "\033[90m"

# ── Team data ─────────────────────────────────────────────────────────────────
TEAMS = {
    "ARI": (109, "Arizona Diamondbacks",    RED),
    "ATL": (144, "Atlanta Braves",          BLUE),
    "BAL": (110, "Baltimore Orioles",       YELLOW),
    "BOS": (111, "Boston Red Sox",          RED),
    "CHC": (112, "Chicago Cubs",            BLUE),
    "CWS": (145, "Chicago White Sox",       WHITE),
    "CIN": (113, "Cincinnati Reds",         RED),
    "CLE": (114, "Cleveland Guardians",     RED),
    "COL": (115, "Colorado Rockies",        CYAN),
    "DET": (116, "Detroit Tigers",          BLUE),
    "HOU": (117, "Houston Astros",          YELLOW),
    "KC":  (118, "Kansas City Royals",      BLUE),
    "LAA": (108, "Los Angeles Angels",      RED),
    "LAD": (119, "Los Angeles Dodgers",     BLUE),
    "MIA": (146, "Miami Marlins",           CYAN),
    "MIL": (158, "Milwaukee Brewers",       YELLOW),
    "MIN": (142, "Minnesota Twins",         RED),
    "NYM": (121, "New York Mets",           BLUE),
    "NYY": (147, "New York Yankees",        BLUE),
    "OAK": (133, "Oakland Athletics",       GREEN),
    "PHI": (143, "Philadelphia Phillies",   RED),
    "PIT": (134, "Pittsburgh Pirates",      YELLOW),
    "SD":  (135, "San Diego Padres",        YELLOW),
    "SF":  (137, "San Francisco Giants",    YELLOW),
    "SEA": (136, "Seattle Mariners",        CYAN),
    "STL": (138, "St. Louis Cardinals",     RED),
    "TB":  (139, "Tampa Bay Rays",          BLUE),
    "TEX": (140, "Texas Rangers",           BLUE),
    "TOR": (141, "Toronto Blue Jays",       BLUE),
    "WSH": (120, "Washington Nationals",    RED),
}

TEAM_ID_TO_ABV = {v[0]: k for k, v in TEAMS.items()}

MLB_API = "https://statsapi.mlb.com/api/v1"


# ── API helpers ───────────────────────────────────────────────────────────────
def fetch_schedule(date_str: str, team_id: int | None = None) -> dict:
    params = {
        "sportId": 1,
        "date": date_str,
        "hydrate": "linescore,team",
    }
    if team_id:
        params["teamId"] = team_id
    resp = requests.get(f"{MLB_API}/schedule", params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()


def fetch_standings() -> dict:
    resp = requests.get(
        f"{MLB_API}/standings",
        params={"leagueId": "103,104", "standingsTypes": "regularSeason", "hydrate": "team,division"},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


# ── Formatting helpers ────────────────────────────────────────────────────────
def team_color(abv: str) -> str:
    return TEAMS.get(abv, (None, None, WHITE))[2]


def abv_from_id(team_id: int) -> str:
    return TEAM_ID_TO_ABV.get(team_id, "???")


def fmt_team(abv: str, width: int = 3) -> str:
    color = team_color(abv)
    return f"{BOLD}{color}{abv:<{width}}{RESET}"


def fmt_score(score: int | None) -> str:
    if score is None:
        return "  -"
    return f"{score:>3}"


def game_status_color(status: str) -> str:
    s = status.lower()
    if "final" in s:
        return GRAY
    if "in progress" in s or "live" in s:
        return GREEN
    if "postponed" in s or "suspended" in s:
        return YELLOW
    return CYAN


def parse_date(s: str) -> date:
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m-%d-%Y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    try:
        month, day = s.split("/")
        return date(date.today().year, int(month), int(day))
    except (ValueError, TypeError):
        pass
    raise ValueError(f"Unrecognized date format: {s}")


# ── Renderers (write to a buffer so server can capture output) ────────────────
def render_schedule(date_str: str, team_abv: str | None = None, out=None) -> str:
    buf = io.StringIO()
    _out = out or buf

    def p(s=""):
        print(s, file=_out)

    team_id = None
    if team_abv:
        abv = team_abv.upper()
        if abv not in TEAMS:
            p(f"{RED}Unknown team: {abv}{RESET}  —  try: curl mlbsched.run/teams")
            return buf.getvalue()
        team_id = TEAMS[abv][0]

    data = fetch_schedule(date_str, team_id)

    d = datetime.strptime(date_str, "%Y-%m-%d")
    label = d.strftime("%A, %B %-d, %Y")
    if team_abv:
        abv = team_abv.upper()
        color = team_color(abv)
        title = f"{BOLD}{color}{TEAMS[abv][1]}{RESET} — {BOLD}{WHITE}{label}{RESET}"
    else:
        title = f"{BOLD}{CYAN}MLB Schedule{RESET} — {BOLD}{WHITE}{label}{RESET}"

    p()
    p(f"  {title}")
    p(f"  {GRAY}{'─' * 52}{RESET}")

    total_games = sum(d.get("totalGames", 0) for d in data.get("dates", []))
    if total_games == 0:
        p(f"  {GRAY}No games scheduled.{RESET}")
        p()
        return buf.getvalue()

    for date_block in data.get("dates", []):
        for game in date_block.get("games", []):
            _render_game_line(game, _out)

    p()
    return buf.getvalue()


def _render_game_line(game: dict, out=None):
    away_id   = game["teams"]["away"]["team"]["id"]
    home_id   = game["teams"]["home"]["team"]["id"]
    away_abv  = abv_from_id(away_id)
    home_abv  = abv_from_id(home_id)

    status    = game["status"]["detailedState"]
    abstract  = game["status"]["abstractGameState"]

    away_score = game["teams"]["away"].get("score")
    home_score = game["teams"]["home"].get("score")

    linescore   = game.get("linescore", {})
    inning      = linescore.get("currentInning")
    inning_half = linescore.get("inningHalf", "")

    game_time = ""
    if abstract == "Preview":
        gt = game.get("gameDate", "")
        if gt:
            try:
                dt = datetime.strptime(gt, "%Y-%m-%dT%H:%M:%SZ")
                dt_et = dt.replace(hour=(dt.hour - 4) % 24)
                game_time = dt_et.strftime("%-I:%M %p ET")
            except Exception:
                pass

    away_str = fmt_team(away_abv)
    home_str = fmt_team(home_abv)

    if abstract == "Final":
        a_sc  = f"{BOLD}{fmt_score(away_score)}{RESET}"
        h_sc  = f"{BOLD}{fmt_score(home_score)}{RESET}"
        state = f"{GRAY}Final{RESET}"
        if away_score is not None and home_score is not None:
            if away_score > home_score:
                a_sc = f"{BOLD}{WHITE}{fmt_score(away_score)}{RESET}"
            else:
                h_sc = f"{BOLD}{WHITE}{fmt_score(home_score)}{RESET}"
    elif abstract == "Live":
        a_sc  = f"{GREEN}{fmt_score(away_score)}{RESET}"
        h_sc  = f"{GREEN}{fmt_score(home_score)}{RESET}"
        arrow = "▲" if inning_half.lower() == "top" else "▼"
        state = f"{GREEN}{BOLD}{arrow}{inning}{RESET}"
    else:
        a_sc  = f"{GRAY}  -{RESET}"
        h_sc  = f"{GRAY}  -{RESET}"
        state = f"{CYAN}{game_time}{RESET}" if game_time else f"{GRAY}{status}{RESET}"

    print(f"  {away_str} {a_sc}  {DIM}@{RESET}  {home_str} {h_sc}   {state}", file=out)


def render_standings(out=None) -> str:
    buf = io.StringIO()
    _out = out or buf

    def p(s=""):
        print(s, file=_out)

    data = fetch_standings()

    p()
    p(f"  {BOLD}{CYAN}MLB Standings{RESET}")
    p(f"  {GRAY}{'─' * 52}{RESET}")

    for record in data.get("records", []):
        div = record.get("division", {}).get("name", "Unknown Division")
        p(f"\n  {BOLD}{YELLOW}{div}{RESET}")
        p(f"  {GRAY}{'Team':<22} {'W':>3} {'L':>3} {'PCT':>5} {'GB':>5}{RESET}")

        teams = sorted(record.get("teamRecords", []), key=lambda x: -float(x.get("winningPercentage", 0)))
        for i, t in enumerate(teams):
            team_id = t["team"]["id"]
            abv     = abv_from_id(team_id)
            name    = TEAMS.get(abv, (None, t["team"]["name"], WHITE))[1]
            wins    = t.get("wins", 0)
            losses  = t.get("losses", 0)
            pct     = t.get("winningPercentage", ".000")
            gb      = t.get("gamesBack", "-")
            color   = team_color(abv)
            marker  = f"{BOLD}{color}" if i == 0 else RESET
            p(f"  {marker}{name:<22}{RESET} {wins:>3} {losses:>3} {pct:>5} {gb:>5}")

    p()
    return buf.getvalue()


def render_team_list(out=None) -> str:
    buf = io.StringIO()
    _out = out or buf

    def p(s=""):
        print(s, file=_out)

    p(f"\n  {BOLD}Valid team abbreviations:{RESET}")
    abvs = sorted(TEAMS.keys())
    for i in range(0, len(abvs), 6):
        row = "  ".join(f"{BOLD}{team_color(a)}{a}{RESET}" for a in abvs[i:i+6])
        p(f"  {row}")
    p()
    return buf.getvalue()


def render_live(out=None) -> str:
    buf = io.StringIO()
    _out = out or buf

    def p(s=""):
        print(s, file=_out)

    today = date.today().strftime("%Y-%m-%d")
    data = fetch_schedule(today)

    live_games = [
        game
        for date_block in data.get("dates", [])
        for game in date_block.get("games", [])
        if game["status"]["abstractGameState"] == "Live"
    ]

    p()
    p(f"  {BOLD}{GREEN}Live Scores{RESET} — {BOLD}{WHITE}{date.today().strftime('%A, %B %-d, %Y')}{RESET}")
    p(f"  {GRAY}{'─' * 52}{RESET}")

    if not live_games:
        p(f"  {GRAY}No games in progress right now.{RESET}")
    else:
        for game in live_games:
            _render_game_line(game, _out)

    p()
    return buf.getvalue()


def render_smart_today(out=None) -> tuple[str, bool]:
    """Combined view for the root endpoint. Returns (content, has_live_games)."""
    buf = io.StringIO()
    _out = out or buf

    def p(s=""):
        print(s, file=_out)

    today = date.today()
    date_str = today.strftime("%Y-%m-%d")
    label = today.strftime("%A, %B %-d, %Y")

    data = fetch_schedule(date_str)

    all_games = [
        game
        for date_block in data.get("dates", [])
        for game in date_block.get("games", [])
    ]
    live_games = [g for g in all_games if g["status"]["abstractGameState"] == "Live"]
    has_live = bool(live_games)

    if has_live:
        p()
        p(f"  {BOLD}{GREEN}● Live Now{RESET} — {BOLD}{WHITE}{label}{RESET}")
        p(f"  {GRAY}{'─' * 52}{RESET}")
        for game in live_games:
            _render_game_line(game, _out)

        p()
        p(f"  {BOLD}{CYAN}Today's Schedule{RESET}")
        p(f"  {GRAY}{'─' * 52}{RESET}")
        for game in all_games:
            _render_game_line(game, _out)
        p()
    else:
        title = f"{BOLD}{CYAN}MLB Schedule{RESET} — {BOLD}{WHITE}{label}{RESET}"
        p()
        p(f"  {title}")
        p(f"  {GRAY}{'─' * 52}{RESET}")
        if not all_games:
            p(f"  {GRAY}No games scheduled.{RESET}")
        else:
            for game in all_games:
                _render_game_line(game, _out)
        p()

    return buf.getvalue(), has_live


def render_help(out=None) -> str:
    buf = io.StringIO()
    _out = out or buf

    print(f"""
  {BOLD}{CYAN}mlbsched.run{RESET} — MLB schedule in your terminal

  {BOLD}Usage:{RESET}
    curl mlbsched.run                      Today's full schedule
    curl mlbsched.run/<TEAM>               Team's game today        (e.g. NYY)
    curl mlbsched.run/<TEAM>/<DATE>        Team on a specific date
    curl mlbsched.run/<DATE>               Full schedule on a date  (YYYY-MM-DD)
    curl mlbsched.run/tomorrow             Tomorrow's schedule
    curl mlbsched.run/tomorrow/<TEAM>      Team tomorrow
    curl mlbsched.run/live                 All games in progress right now
    curl mlbsched.run/standings            Division standings
    curl mlbsched.run/teams                All team abbreviations

  {BOLD}Examples:{RESET}
    curl mlbsched.run
    curl mlbsched.run/NYY
    curl mlbsched.run/NYY/2026-04-20
    curl mlbsched.run/standings
""", file=_out)
    return buf.getvalue()


# ── CLI entry point ───────────────────────────────────────────────────────────
def main():
    args = sys.argv[1:]

    if not args:
        render_schedule(date.today().strftime("%Y-%m-%d"), out=sys.stdout)
        return

    first = args[0].lower()

    if first in ("-h", "--help", "help"):
        render_help(out=sys.stdout)
        return

    if first == "teams":
        render_team_list(out=sys.stdout)
        return

    if first == "standings":
        render_standings(out=sys.stdout)
        return

    if first == "tomorrow":
        team = args[1].upper() if len(args) > 1 else None
        d = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")
        render_schedule(d, team, out=sys.stdout)
        return

    try:
        d = parse_date(args[0])
        render_schedule(d.strftime("%Y-%m-%d"), out=sys.stdout)
        return
    except ValueError:
        pass

    team_abv = args[0].upper()
    if len(args) > 1:
        try:
            d = parse_date(args[1])
            render_schedule(d.strftime("%Y-%m-%d"), team_abv, out=sys.stdout)
        except ValueError:
            print(f"{RED}Could not parse date: {args[1]}{RESET}")
    else:
        render_schedule(date.today().strftime("%Y-%m-%d"), team_abv, out=sys.stdout)


if __name__ == "__main__":
    main()
