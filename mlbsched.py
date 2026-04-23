#!/usr/bin/env python3
"""mlbsched - MLB schedule in your terminal"""

import sys
import io
import math
from datetime import date, datetime, timedelta, timezone as _UTC
from zoneinfo import ZoneInfo
import requests

ET = ZoneInfo("America/New_York")


def today_et() -> date:
    """Return today's date in ET, switching to next day only after 1am ET."""
    now = datetime.now(ET)
    if now.hour < 1:
        return (now - timedelta(days=1)).date()
    return now.date()

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

# Stadium name, latitude, longitude
STADIUMS = {
    "ARI": ("Chase Field",                  33.4453, -112.0667),
    "ATL": ("Truist Park",                  33.8908,  -84.4679),
    "BAL": ("Oriole Park at Camden Yards",  39.2839,  -76.6218),
    "BOS": ("Fenway Park",                  42.3467,  -71.0972),
    "CHC": ("Wrigley Field",                41.9484,  -87.6553),
    "CWS": ("Guaranteed Rate Field",        41.8300,  -87.6339),
    "CIN": ("Great American Ball Park",     39.0974,  -84.5069),
    "CLE": ("Progressive Field",            41.4962,  -81.6852),
    "COL": ("Coors Field",                  39.7559, -104.9942),
    "DET": ("Comerica Park",                42.3390,  -83.0485),
    "HOU": ("Minute Maid Park",             29.7572,  -95.3556),
    "KC":  ("Kauffman Stadium",             39.0517,  -94.4803),
    "LAA": ("Angel Stadium",                33.8003, -117.8827),
    "LAD": ("Dodger Stadium",               34.0739, -118.2400),
    "MIA": ("loanDepot Park",               25.7781,  -80.2197),
    "MIL": ("American Family Field",        43.0280,  -87.9712),
    "MIN": ("Target Field",                 44.9817,  -93.2783),
    "NYM": ("Citi Field",                   40.7571,  -73.8458),
    "NYY": ("Yankee Stadium",               40.8296,  -73.9262),
    "OAK": ("Sutter Health Park",           38.5768, -121.5085),
    "PHI": ("Citizens Bank Park",           39.9057,  -75.1665),
    "PIT": ("PNC Park",                     40.4469,  -80.0057),
    "SD":  ("Petco Park",                   32.7076, -117.1570),
    "SF":  ("Oracle Park",                  37.7786, -122.3893),
    "SEA": ("T-Mobile Park",                47.5914, -122.3325),
    "STL": ("Busch Stadium",                38.6226,  -90.1928),
    "TB":  ("Tropicana Field",              27.7683,  -82.6534),
    "TEX": ("Globe Life Field",             32.7473,  -97.0820),
    "TOR": ("Rogers Centre",                43.6414,  -79.3894),
    "WSH": ("Nationals Park",               38.8730,  -77.0074),
}

# Known neutral/international venues: venue name (as returned by MLB API) → (display_name, lat, lon)
SPECIAL_VENUES: dict[str, tuple[str, float, float]] = {
    "London Stadium":                    ("London Stadium",                   51.5386,   -0.0163),
    "Estadio Alfredo Harp Helú":         ("Estadio Alfredo Harp Helú",        19.4897,  -99.1539),
    "Tokyo Dome":                        ("Tokyo Dome",                       35.7056,  139.7519),
    "Estadio de Béisbol Monterrey":      ("Estadio de Béisbol Monterrey",     25.6866, -100.3161),
    "Estadio LoanMart Field":            ("Estadio LoanMart Field",           34.1427, -117.8346),
    "Rickwood Field":                    ("Rickwood Field",                   33.5200,  -86.8354),
    "Hiram Bithorn Stadium":             ("Hiram Bithorn Stadium",            18.4284,  -66.0676),
}

MLB_API = "https://statsapi.mlb.com/api/v1"


# ── API helpers ───────────────────────────────────────────────────────────────
def fetch_schedule(date_str: str, team_id: int | None = None) -> dict:
    params = {
        "sportId": 1,
        "date": date_str,
        "hydrate": "linescore,team,venue,probablePitcher",
    }
    if team_id:
        params["teamId"] = team_id
    resp = requests.get(f"{MLB_API}/schedule", params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    _enrich_probable_pitchers(data)
    return data


def _enrich_probable_pitchers(schedule_data: dict) -> None:
    """Splice season W-L and ERA into each probablePitcher dict (in place)."""
    ids: list[int] = []
    pitchers: list[dict] = []
    for date_block in schedule_data.get("dates", []):
        for game in date_block.get("games", []):
            for side in ("away", "home"):
                pp = game["teams"][side].get("probablePitcher")
                if pp and pp.get("id"):
                    ids.append(pp["id"])
                    pitchers.append(pp)
    if not ids:
        return

    season = today_et().year
    try:
        resp = requests.get(
            f"{MLB_API}/people",
            params={
                "personIds": ",".join(str(i) for i in ids),
                "hydrate":   f"stats(group=[pitching],type=[season],season={season})",
            },
            timeout=10,
        )
        resp.raise_for_status()
        people = resp.json().get("people", [])
    except requests.RequestException:
        return

    stats_by_id: dict[int, dict] = {}
    for p in people:
        pid = p.get("id")
        if not pid:
            continue
        for block in p.get("stats", []):
            for split in block.get("splits", []):
                s = split.get("stat", {})
                stats_by_id[pid] = {
                    "wins":   s.get("wins"),
                    "losses": s.get("losses"),
                    "era":    s.get("era"),
                }
                break
            if pid in stats_by_id:
                break

    for pp in pitchers:
        st = stats_by_id.get(pp["id"])
        if st:
            pp["_record"] = st


def fetch_standings() -> dict:
    resp = requests.get(
        f"{MLB_API}/standings",
        params={"leagueId": "103,104", "standingsTypes": "regularSeason", "hydrate": "team,division"},
        timeout=10,
    )
    resp.raise_for_status()
    return resp.json()


# ── Distance helpers ──────────────────────────────────────────────────────────
def game_location(game: dict) -> tuple[str, float, float] | None:
    """Return (venue_name, lat, lon) for a game, handling neutral/international sites."""
    venue_name = game.get("venue", {}).get("name", "")

    if venue_name in SPECIAL_VENUES:
        return SPECIAL_VENUES[venue_name]

    home_id  = game["teams"]["home"]["team"]["id"]
    home_abv = abv_from_id(home_id)
    stadium  = STADIUMS.get(home_abv)
    if stadium:
        return stadium  # (name, lat, lon)

    return None


def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in miles between two lat/lon points."""
    R = 3958.8
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.asin(math.sqrt(a))


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
        return date(today_et().year, int(month), int(day))
    except (ValueError, TypeError):
        pass
    raise ValueError(f"Unrecognized date format: {s}")


def fmt_game_time(gt_str: str, tz: ZoneInfo | None = None) -> str:
    if not gt_str:
        return ""
    try:
        dt_utc = datetime.strptime(gt_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_UTC.utc)
        dt_local = dt_utc.astimezone(tz or ET)
        return dt_local.strftime("%-I:%M %p %Z")
    except Exception:
        return ""


# ── Renderers (write to a buffer so server can capture output) ────────────────
def render_schedule(date_str: str, team_abv: str | None = None, out=None, tz: ZoneInfo | None = None) -> str:
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
            _render_game_line(game, _out, tz=tz)

    p()
    return buf.getvalue()


def _render_game_line(game: dict, out=None, dist_label: str | None = None, tz: ZoneInfo | None = None):
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
        game_time = fmt_game_time(game.get("gameDate", ""), tz)

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

    suffix = f"   {dist_label}" if dist_label else ""
    print(f"  {away_str} {a_sc}  {DIM}@{RESET}  {home_str} {h_sc}   {state}{suffix}", file=out)

    if abstract == "Preview":
        away_pp = game["teams"]["away"].get("probablePitcher")
        home_pp = game["teams"]["home"].get("probablePitcher")
        if away_pp and home_pp:
            a = _fmt_pitcher(away_pp)
            h = _fmt_pitcher(home_pp)
            print(f"         {GRAY}{a}  vs  {h}{RESET}", file=out)


def _fmt_pitcher(pp: dict) -> str:
    name = pp.get("fullName", "TBD")
    rec  = pp.get("_record")
    if not rec:
        return name
    w, l, era = rec.get("wins"), rec.get("losses"), rec.get("era")
    if w is None or l is None or era is None:
        return name
    return f"{name} ({w}-{l}, {era})"


def render_boxscore(game: dict, out=None) -> str:
    """Render inning-by-inning line score for a Final game. Returns empty string otherwise."""
    buf = io.StringIO()
    _out = out or buf

    def p(s=""):
        print(s, file=_out)

    if game["status"]["abstractGameState"] != "Final":
        return buf.getvalue()

    ls = game.get("linescore", {})
    innings = ls.get("innings", [])
    if not innings:
        return buf.getvalue()

    away_abv = abv_from_id(game["teams"]["away"]["team"]["id"])
    home_abv = abv_from_id(game["teams"]["home"]["team"]["id"])

    teams_totals = ls.get("teams", {})
    away_totals  = teams_totals.get("away", {})
    home_totals  = teams_totals.get("home", {})
    away_runs    = away_totals.get("runs", 0)
    home_runs    = home_totals.get("runs", 0)

    def fmt_inning(side: dict | None) -> str:
        if not side or side.get("runs") is None:
            return f"{'-':>2}"
        return f"{side['runs']:>2}"

    header_nums = " ".join(f"{i.get('num', '?'):>2}" for i in innings)
    p(f"      {GRAY}     {header_nums}   R  H  E{RESET}")

    def row(abv: str, side_key: str, totals: dict, is_winner: bool):
        inning_runs = " ".join(fmt_inning(i.get(side_key)) for i in innings)
        r = totals.get("runs", 0)
        h = totals.get("hits", 0)
        e = totals.get("errors", 0)
        color = f"{BOLD}{WHITE}" if is_winner else GRAY
        return (
            f"      {fmt_team(abv)}  {GRAY}{inning_runs}{RESET}   "
            f"{color}{r:>2}{RESET} {GRAY}{h:>2} {e:>2}{RESET}"
        )

    p(row(away_abv, "away", away_totals, away_runs > home_runs))
    p(row(home_abv, "home", home_totals, home_runs > away_runs))

    return buf.getvalue()


def render_team_recap(date_str: str, team_abv: str, out=None, tz: ZoneInfo | None = None) -> str:
    """Score line + boxscore for each of a team's games on a given date. Reusable by /box/{team}/{date}."""
    buf = io.StringIO()
    _out = out or buf

    def p(s=""):
        print(s, file=_out)

    abv = team_abv.upper()
    if abv not in TEAMS:
        p(f"{RED}Unknown team: {abv}{RESET}  —  try: curl mlbsched.run/teams")
        return buf.getvalue()

    team_id = TEAMS[abv][0]
    data    = fetch_schedule(date_str, team_id)

    d     = datetime.strptime(date_str, "%Y-%m-%d")
    label = d.strftime("%A, %B %-d, %Y")
    color = team_color(abv)

    p()
    p(f"  {BOLD}{color}{TEAMS[abv][1]}{RESET} — {BOLD}{WHITE}{label}{RESET}")
    p(f"  {GRAY}{'─' * 52}{RESET}")

    games = [g for block in data.get("dates", []) for g in block.get("games", [])]
    if not games:
        p(f"  {GRAY}No games scheduled.{RESET}")
        p()
        return buf.getvalue()

    for game in games:
        _render_game_line(game, _out, tz=tz)
        box = render_boxscore(game)
        if box:
            print(box, file=_out, end="")

    p()
    return buf.getvalue()


def render_distance(user_lat: float, user_lon: float, user_city: str, out=None, tz: ZoneInfo | None = None) -> str:
    buf = io.StringIO()
    _out = out or buf

    def p(s=""):
        print(s, file=_out)

    today = today_et()
    date_str = today.strftime("%Y-%m-%d")
    data = fetch_schedule(date_str)

    all_games = [
        game
        for date_block in data.get("dates", [])
        for game in date_block.get("games", [])
    ]

    games_with_dist = []
    for game in all_games:
        loc = game_location(game)
        if loc:
            venue_name, slat, slon = loc
            dist = haversine(user_lat, user_lon, slat, slon)
        else:
            venue_name = "Unknown Stadium"
            dist = float("inf")
        games_with_dist.append((dist, game, venue_name))

    games_with_dist.sort(key=lambda x: x[0])

    p()
    p(f"  {BOLD}{CYAN}Nearest Games Today{RESET} — {BOLD}{WHITE}{today.strftime('%A, %B %-d, %Y')}{RESET}")
    p(f"  {GRAY}Nearest to: {user_city}{RESET}")
    p(f"  {GRAY}{'─' * 60}{RESET}")

    for dist, game, stadium_name in games_with_dist:
        home_abv = abv_from_id(game["teams"]["home"]["team"]["id"])
        if dist < 50:
            dist_color = GREEN
        elif dist < 300:
            dist_color = YELLOW
        else:
            dist_color = GRAY
        dist_str = f"{dist:,.0f} mi" if dist != float("inf") else "? mi"
        dist_label = f"{dist_color}{dist_str}{RESET}  {GRAY}{stadium_name}{RESET}"
        _render_game_line(game, _out, dist_label=dist_label, tz=tz)

    p()
    return buf.getvalue()


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


def render_live(out=None, tz: ZoneInfo | None = None) -> str:
    buf = io.StringIO()
    _out = out or buf

    def p(s=""):
        print(s, file=_out)

    today = today_et()
    date_str = today.strftime("%Y-%m-%d")
    data = fetch_schedule(date_str)

    live_games = [
        game
        for date_block in data.get("dates", [])
        for game in date_block.get("games", [])
        if game["status"]["abstractGameState"] == "Live"
    ]

    p()
    p(f"  {BOLD}{GREEN}Live Scores{RESET} — {BOLD}{WHITE}{today.strftime('%A, %B %-d, %Y')}{RESET}")
    p(f"  {GRAY}{'─' * 52}{RESET}")

    if not live_games:
        p(f"  {GRAY}No games in progress right now.{RESET}")
    else:
        for game in live_games:
            _render_game_line(game, _out, tz=tz)

    p()
    return buf.getvalue()


def render_smart_today(out=None, tz: ZoneInfo | None = None) -> tuple[str, bool]:
    """Combined view for the root endpoint. Returns (content, has_live_games)."""
    buf = io.StringIO()
    _out = out or buf

    def p(s=""):
        print(s, file=_out)

    today = today_et()
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
            _render_game_line(game, _out, tz=tz)

        p()
        p(f"  {BOLD}{CYAN}Today's Schedule{RESET}")
        p(f"  {GRAY}{'─' * 52}{RESET}")
        for game in all_games:
            _render_game_line(game, _out, tz=tz)
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
                _render_game_line(game, _out, tz=tz)
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
    curl mlbsched.run/yesterday            Yesterday's scores
    curl mlbsched.run/yesterday/<TEAM>     Team yesterday
    curl mlbsched.run/tomorrow             Tomorrow's schedule
    curl mlbsched.run/tomorrow/<TEAM>      Team tomorrow
    curl mlbsched.run/live                 All games in progress right now
    curl mlbsched.run/box/<TEAM>           Yesterday's boxscore for a team
    curl mlbsched.run/box/<TEAM>/<DATE>    Boxscore for a team on a specific date
    curl mlbsched.run/distance             Today's games sorted by distance from you
    curl mlbsched.run/odds                 Today's odds — best NY sportsbook price per market
    curl mlbsched.run/odds/<TEAM>          Odds for one team's game today
    curl mlbsched.run/weather              Current weather at each stadium
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
        render_schedule(today_et().strftime("%Y-%m-%d"), out=sys.stdout)
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

    if first == "yesterday":
        team = args[1].upper() if len(args) > 1 else None
        d = (today_et() - timedelta(days=1)).strftime("%Y-%m-%d")
        render_schedule(d, team, out=sys.stdout)
        return

    if first == "tomorrow":
        team = args[1].upper() if len(args) > 1 else None
        d = (today_et() + timedelta(days=1)).strftime("%Y-%m-%d")
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
        render_schedule(today_et().strftime("%Y-%m-%d"), team_abv, out=sys.stdout)


if __name__ == "__main__":
    main()
