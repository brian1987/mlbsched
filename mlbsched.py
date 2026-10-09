#!/usr/bin/env python3
"""mlbsched - MLB schedule in your terminal"""

import sys
import io
import math
import time
import random
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
    "ATH": (133, "Athletics",               GREEN),
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

# Historical franchise names returned by the MLB API for old games. Maps the
# name as the API gives it → the abbreviation that franchise actually used at
# the time (so 1976 Expos show MON, not WSH; 1957 Dodgers show BRO, not LAD).
HISTORICAL_NAME_TO_ABV = {
    "Brooklyn Dodgers":              "BRO",
    "New York Giants":               "NYG",
    "Boston Braves":                 "BSN",
    "Milwaukee Braves":              "MLN",
    "Philadelphia Athletics":        "PHA",
    "Kansas City Athletics":         "KCA",
    "St. Louis Browns":              "SLB",
    "Washington Senators":           "WSH",
    "Seattle Pilots":                "SEP",
    "Montreal Expos":                "MON",
    "Houston Colt .45s":             "HOU",
    "California Angels":             "CAL",
    "Anaheim Angels":                "ANA",
    "Los Angeles Angels of Anaheim": "LAA",
    "Florida Marlins":               "FLA",
    "Tampa Bay Devil Rays":          "TBD",
    "Cincinnati Redlegs":            "CIN",
}

# Inherited color for historical abvs (matches the current franchise).
HISTORICAL_ABV_COLOR = {
    "BRO": BLUE,    "NYG": YELLOW,  "BSN": BLUE,    "MLN": BLUE,
    "PHA": GREEN,   "KCA": GREEN,   "SLB": YELLOW,  "SEP": YELLOW,
    "MON": RED,     "CAL": RED,     "ANA": RED,
    "FLA": CYAN,    "TBD": BLUE,
}

# Stadium name, latitude, longitude
STADIUMS = {
    "ARI": ("Chase Field",                  33.4453, -112.0667),
    "ATL": ("Truist Park",                  33.8908,  -84.4679),
    "BAL": ("Oriole Park at Camden Yards",  39.2839,  -76.6218),
    "BOS": ("Fenway Park",                  42.3467,  -71.0972),
    "CHC": ("Wrigley Field",                41.9484,  -87.6553),
    "CWS": ("Rate Field",                   41.8300,  -87.6339),
    "CIN": ("Great American Ball Park",     39.0974,  -84.5069),
    "CLE": ("Progressive Field",            41.4962,  -81.6852),
    "COL": ("Coors Field",                  39.7559, -104.9942),
    "DET": ("Comerica Park",                42.3390,  -83.0485),
    "HOU": ("Daikin Park",                  29.7572,  -95.3556),
    "KC":  ("Kauffman Stadium",             39.0517,  -94.4803),
    "LAA": ("Angel Stadium",                33.8003, -117.8827),
    "LAD": ("Dodger Stadium",               34.0739, -118.2400),
    "MIA": ("loanDepot park",               25.7781,  -80.2197),
    "MIL": ("American Family Field",        43.0280,  -87.9712),
    "MIN": ("Target Field",                 44.9817,  -93.2783),
    "NYM": ("Citi Field",                   40.7571,  -73.8458),
    "NYY": ("Yankee Stadium",               40.8296,  -73.9262),
    "ATH": ("Sutter Health Park",           38.5768, -121.5085),
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
# ── schedule fetch: short TTL cache + serve-stale-on-error ──────────────────────
# Collapses a burst of requests (e.g. a front-page traffic spike) into ~1 upstream
# call, and serves the last good payload if the MLB API hiccups rather than failing
# the request. Keyed by (date, team); bounded so a flood of distinct dates can't
# grow it without limit.
_SCHED_TTL_SECONDS = 45
_sched_cache: dict[tuple, tuple[float, dict]] = {}   # key -> (fetched_at_monotonic, data)


def fetch_schedule(date_str: str, team_id: int | None = None) -> dict:
    key = (date_str, team_id)
    now = time.monotonic()
    cached = _sched_cache.get(key)
    if cached is not None and now - cached[0] < _SCHED_TTL_SECONDS:
        return cached[1]

    params = {
        "sportId": 1,
        "date": date_str,
        "hydrate": "linescore,team,venue(location),probablePitcher",
    }
    if team_id:
        params["teamId"] = team_id
    try:
        resp = requests.get(f"{MLB_API}/schedule", params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException:
        if cached is not None:
            return cached[1]   # stale beats a 500 — serve the last good payload
        raise

    _enrich_probable_pitchers(data)
    if len(_sched_cache) > 256:      # drop expired entries before they accumulate
        for k, (t, _) in list(_sched_cache.items()):
            if now - t >= _SCHED_TTL_SECONDS:
                del _sched_cache[k]
    _sched_cache[key] = (now, data)
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

    season = stats_season()
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
    hand_by_id: dict[int, str] = {}
    for p in people:
        pid = p.get("id")
        if not pid:
            continue
        hand = (p.get("pitchHand") or {}).get("code")
        if hand:
            hand_by_id[pid] = hand
        for block in p.get("stats", []):
            for split in block.get("splits", []):
                s = split.get("stat", {})
                stats_by_id[pid] = {
                    "wins":   s.get("wins"),
                    "losses": s.get("losses"),
                    "era":    s.get("era"),
                    "whip":   s.get("whip"),
                }
                break
            if pid in stats_by_id:
                break

    for pp in pitchers:
        st = stats_by_id.get(pp["id"])
        if st:
            pp["_record"] = st
        hand = hand_by_id.get(pp["id"])
        if hand:
            pp["_hand"] = hand


# ── Next game ─────────────────────────────────────────────────────────────────
_NEXT_WINDOW_DAYS = 45          # covers the All-Star break and the offseason edge
_NEXT_TTL_SECONDS = 60
_next_cache: dict[int, tuple[float, dict | None]] = {}   # team_id -> (fetched_at, game)


def _is_no_play(game: dict) -> bool:
    s = (game.get("status", {}).get("detailedState") or "").lower()
    return "postponed" in s or "cancel" in s or "suspended" in s


def fetch_next_game(team_id: int) -> dict | None:
    """The team's next game that isn't over: today's if it's still to come or in
    progress, otherwise the first upcoming one within the window. None when
    nothing is scheduled (offseason, or eliminated with the season done)."""
    now = time.monotonic()
    cached = _next_cache.get(team_id)
    if cached is not None and now - cached[0] < _NEXT_TTL_SECONDS:
        return cached[1]
    today = today_et()
    try:
        resp = requests.get(
            f"{MLB_API}/schedule",
            params={
                "sportId":   1,
                "teamId":    team_id,
                "startDate": today.strftime("%Y-%m-%d"),
                "endDate":   (today + timedelta(days=_NEXT_WINDOW_DAYS)).strftime("%Y-%m-%d"),
                "hydrate":   "linescore,team,venue(location),probablePitcher",
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException:
        if cached is not None:
            return cached[1]
        raise
    game = next(
        (g for block in data.get("dates", []) for g in block.get("games", [])
         if g["status"]["abstractGameState"] != "Final" and not _is_no_play(g)),
        None,
    )
    if game is not None:
        _enrich_probable_pitchers({"dates": [{"games": [game]}]})
    _next_cache[team_id] = (now, game)
    return game


def countdown_label(gt_str: str, now: datetime | None = None) -> str:
    """'in 3h 12m', 'in 2d 4h', or '' once the time has passed / is unknown."""
    try:
        start = datetime.strptime(gt_str, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_UTC.utc)
    except (ValueError, TypeError):
        return ""
    delta = start - (now or datetime.now(_UTC.utc))
    secs = int(delta.total_seconds())
    if secs <= 0:
        return ""
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    mins = rem // 60
    if days:
        return f"in {days}d {hours}h"
    if hours:
        return f"in {hours}h {mins}m"
    return f"in {mins}m"


def day_label(official_date: str, today: date | None = None) -> str:
    """'Today', 'Tomorrow', or 'Saturday, October 3'."""
    today = today or today_et()
    try:
        d = datetime.strptime(official_date, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return ""
    n = (d - today).days
    if n == 0:
        return "Today"
    if n == 1:
        return "Tomorrow"
    return d.strftime("%A, %B %-d")


def render_next_game(team_abv: str, out=None, tz: ZoneInfo | None = None) -> str:
    buf = io.StringIO()
    _out = out or buf

    def p(s=""):
        print(s, file=_out)

    abv = team_abv.upper()
    if abv not in TEAMS:
        p(f"{RED}Unknown team: {abv}{RESET}  —  try: curl mlbsched.run/teams")
        return buf.getvalue()

    team_id, name, color = TEAMS[abv]
    p()
    p(f"  {BOLD}{color}{name}{RESET} — {BOLD}{WHITE}Next Game{RESET}")
    p(f"  {GRAY}{'─' * 52}{RESET}")

    game = fetch_next_game(team_id)
    if game is None:
        p(f"  {GRAY}No games scheduled in the next {_NEXT_WINDOW_DAYS} days.{RESET}")
        p(f"  {GRAY}Full season: curl mlbsched.run/ical/{abv}.ics{RESET}")
        p()
        return buf.getvalue()

    _render_game_line(game, _out, tz=tz)

    when = day_label(game.get("officialDate") or game.get("gameDate", "")[:10])
    bits = [when] if when else []
    loc = game_location(game)
    if loc:
        home_abv = abv_from_id(game["teams"]["home"]["team"]["id"])
        bits.append(loc[0] if home_abv == abv else f"{loc[0]} (at {home_abv})")
    if game["status"]["abstractGameState"] == "Live":
        bits.append(f"{GREEN}in progress{RESET}{GRAY}")
    elif not game["status"].get("startTimeTBD"):
        cd = countdown_label(game.get("gameDate", ""))
        if cd:
            bits.append(f"first pitch {cd}")
    p(f"  {GRAY}{' · '.join(bits)}{RESET}")
    p()
    return buf.getvalue()


# Standings back /standings, /wildcard and /streaks; same TTL + serve-stale
# treatment as the schedule so a burst is one upstream call and an MLB hiccup
# serves the last good table.
_STANDINGS_TTL_SECONDS = 60
_standings_cache: tuple[float, dict] | None = None   # (fetched_at_monotonic, data)


def fetch_standings() -> dict:
    global _standings_cache
    now = time.monotonic()
    if _standings_cache is not None and now - _standings_cache[0] < _STANDINGS_TTL_SECONDS:
        return _standings_cache[1]
    try:
        resp = requests.get(
            f"{MLB_API}/standings",
            params={"leagueId": "103,104", "standingsTypes": "regularSeason", "hydrate": "team,division"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException:
        if _standings_cache is not None:
            return _standings_cache[1]
        raise
    _standings_cache = (now, data)
    return data


def rank_key(t: dict, field: str) -> tuple[int, float]:
    """Sort key for a standings row: MLB's own rank (tiebreakers applied) first,
    winning percentage as the fallback when the rank isn't published."""
    try:
        rank = int(t.get(field) or 0)
    except (TypeError, ValueError):
        rank = 0
    return (rank if rank > 0 else 10_000, -float(t.get("winningPercentage") or 0))


# MLB's clinchIndicator letters, in the order newspapers print them.
CLINCH_LABELS = {
    "x": "clinched playoff berth",
    "w": "clinched wild card",
    "y": "clinched division",
    "z": "clinched best record in league",
}


def clinch_letter(t: dict) -> str:
    """'x'/'w'/'y'/'z' for a clinched team, '' otherwise. MLB also sends 'e' for
    eliminated teams; the E# column already says that, so it's dropped."""
    c = (t.get("clinchIndicator") or "").lower()
    if c in CLINCH_LABELS:
        return c
    return "x" if t.get("clinched") else ""


# ── Season awareness ──────────────────────────────────────────────────────────
# MLB publishes each season's calendar (spring, Opening Day, All-Star break,
# postseason). Cached 6h; a season's dates don't move mid-day.
_SEASON_TTL_SECONDS = 6 * 60 * 60
_season_dates_cache: dict[int, tuple[float, dict | None]] = {}


def season_dates(year: int) -> dict | None:
    """MLB's calendar for a season, or None if it isn't published (or MLB is down
    and nothing is cached). Keys like regularSeasonStartDate, postSeasonEndDate."""
    now = time.monotonic()
    cached = _season_dates_cache.get(year)
    if cached is not None and now - cached[0] < _SEASON_TTL_SECONDS:
        return cached[1]
    try:
        resp = requests.get(f"{MLB_API}/seasons/{year}", params={"sportId": 1}, timeout=10)
        resp.raise_for_status()
        seasons = resp.json().get("seasons") or []
        info = seasons[0] if seasons else None
    except requests.RequestException:
        return cached[1] if cached is not None else None
    _season_dates_cache[year] = (now, info)
    return info


def season_date(info: dict | None, key: str) -> date | None:
    try:
        return datetime.strptime((info or {})[key], "%Y-%m-%d").date()
    except (KeyError, TypeError, ValueError):
        return None


def stats_season(today: date | None = None) -> int:
    """The season whose stats are current: this year once Opening Day has arrived,
    otherwise last year, so January's /leaders and /player show the season that
    actually happened instead of an empty one. Calendar guess if MLB is unreachable."""
    today = today or today_et()
    opening = season_date(season_dates(today.year), "regularSeasonStartDate")
    if opening:
        return today.year if today >= opening else today.year - 1
    return today.year if (today.month, today.day) >= (3, 20) else today.year - 1


def schedule_season(today: date | None = None) -> int:
    """The season a schedule subscriber wants: next year's as soon as this year's
    postseason is over and MLB has published it."""
    today = today or today_et()
    end = season_date(season_dates(today.year), "postSeasonEndDate")
    if end and today > end and season_dates(today.year + 1):
        return today.year + 1
    return today.year


def season_phase(today: date | None = None) -> str:
    """'preseason' (Jan-Feb), 'spring', 'regular', 'allstar', 'postseason' or
    'offseason'. 'regular' when MLB's calendar is unavailable."""
    today = today or today_et()
    info = season_dates(today.year)
    if not info:
        return "regular"
    spring   = season_date(info, "springStartDate")
    opening  = season_date(info, "regularSeasonStartDate")
    asb_from = season_date(info, "lastDate1stHalf")
    asb_to   = season_date(info, "firstDate2ndHalf")
    reg_end  = season_date(info, "regularSeasonEndDate")
    post_end = season_date(info, "postSeasonEndDate")
    if spring and today < spring:
        return "preseason"
    if opening and today < opening:
        return "spring"
    if asb_from and asb_to and asb_from < today < asb_to:
        return "allstar"
    if reg_end and post_end and reg_end < today <= post_end:
        return "postseason"
    if post_end and today > post_end:
        return "offseason"
    return "regular"


def _days_until(d: date, today: date) -> str:
    n = (d - today).days
    return "today" if n == 0 else ("tomorrow" if n == 1 else f"in {n} days")


def render_no_games_note(today: date, out) -> None:
    """What to say on a day with no games, depending on where we are in the year."""
    def p(s=""):
        print(s, file=out)

    phase = season_phase(today)
    if phase in ("offseason", "preseason"):
        nxt = season_dates(today.year + 1 if phase == "offseason" else today.year)
        spring  = season_date(nxt, "springStartDate")
        opening = season_date(nxt, "regularSeasonStartDate")
        season_label = today.year + 1 if phase == "offseason" else today.year
        p(f"  {GRAY}Offseason.{RESET}")
        if spring and today < spring:
            p(f"  {CYAN}Spring training:{RESET} {spring:%A, %B %-d, %Y}  {GRAY}({_days_until(spring, today)}){RESET}")
        if opening:
            p(f"  {CYAN}Opening Day {season_label}:{RESET} {opening:%A, %B %-d, %Y}  {GRAY}({_days_until(opening, today)}){RESET}")
        last = today.year if phase == "offseason" else today.year - 1
        p(f"  {GRAY}{last} postseason: curl mlbsched.run/postseason/{last}{RESET}")
    elif phase == "postseason":
        p(f"  {GRAY}Postseason off day. Bracket: curl mlbsched.run/postseason{RESET}")
    elif phase == "allstar":
        p(f"  {GRAY}All-Star break. No games scheduled.{RESET}")
    else:
        p(f"  {GRAY}No games scheduled.{RESET}")


# ── Distance helpers ──────────────────────────────────────────────────────────
def game_location(game: dict) -> tuple[str, float, float] | None:
    """Return (venue_name, lat, lon) for a game.

    Prefers what MLB says: the hydrated venue carries the current name (parks get
    renamed most winters — Rate Field, Daikin Park) and coordinates, which also
    covers neutral sites we've never heard of. STADIUMS/SPECIAL_VENUES are the
    fallback for payloads without the hydrate (older cached data, tests)."""
    venue = game.get("venue") or {}
    venue_name = venue.get("name", "")
    coords = ((venue.get("location") or {}).get("defaultCoordinates") or {})
    lat, lon = coords.get("latitude"), coords.get("longitude")
    if venue_name and lat is not None and lon is not None:
        return venue_name, float(lat), float(lon)

    if venue_name in SPECIAL_VENUES:
        return SPECIAL_VENUES[venue_name]

    home_id  = game["teams"]["home"]["team"]["id"]
    home_abv = abv_from_id(home_id)
    stadium  = STADIUMS.get(home_abv)
    if stadium:
        return (venue_name or stadium[0], stadium[1], stadium[2])

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
    if abv in HISTORICAL_ABV_COLOR:
        return HISTORICAL_ABV_COLOR[abv]
    return TEAMS.get(abv, (None, None, WHITE))[2]


def abv_from_id(team_id: int) -> str:
    return TEAM_ID_TO_ABV.get(team_id, "???")


def abv_from_team(team: dict) -> str:
    """Year-aware: prefers a historical name like 'Brooklyn Dodgers' (→ BRO)
    over the current franchise abv (LAD) when the API returns the old name."""
    hist = HISTORICAL_NAME_TO_ABV.get(team.get("name", ""))
    if hist:
        return hist
    return abv_from_id(team.get("id"))


def is_placeholder_team(team: dict) -> bool:
    """True for MLB's unfilled bracket slots ("AL Wild Card #2", "NL 3/6 Winner",
    "Lower Seed League Champion"), which carry ids outside the 30 real clubs."""
    if not team:
        return True
    if team.get("id") in TEAM_ID_TO_ABV:
        return False
    return team.get("name", "") not in HISTORICAL_NAME_TO_ABV


def team_label(team: dict) -> str:
    """Abbreviation for a schedule team dict, historical-name aware; 'TBD' for a
    placeholder slot. Use this instead of abv_from_id when the team may not be
    one of the 30 current clubs (postseason previews, old games)."""
    if is_placeholder_team(team):
        return "TBD"
    return abv_from_team(team)


# Postseason game types → round label. Spring (S) and regular (R) get no tag.
_ROUND_TAG = {"F": "WC", "D": "DS", "L": "CS", "W": "WS"}


def series_tag(game: dict) -> str:
    """Compact series label for a postseason game: 'ALWC G1', 'NLDS G2', 'ALCS G5',
    'WS G7'. Empty for regular-season and spring games."""
    round_ = _ROUND_TAG.get(game.get("gameType", ""))
    if not round_:
        return ""
    desc = game.get("seriesDescription", "") or ""
    league = desc[:2] if desc[:2] in ("AL", "NL") and round_ != "WS" else ""
    tag = f"{league}{round_}"
    num = game.get("seriesGameNumber")
    return f"{tag} G{num}" if num else tag


def game_tag(game: dict) -> str:
    """What distinguishes this game on a schedule line: the postseason series tag,
    or 'G1'/'G2' for a doubleheader (MLB doubleHeader Y = traditional, S = split).
    Empty for an ordinary regular-season game."""
    tag = series_tag(game)
    if tag:
        return tag
    if game.get("doubleHeader") in ("Y", "S") and game.get("gameNumber"):
        return f"G{game['gameNumber']}"
    return ""


def is_doubleheader(game: dict) -> bool:
    return game.get("doubleHeader") in ("Y", "S")


def live_games_now() -> list[dict]:
    """Every game in progress right now.

    today_et() rolls over at 1am ET, but a 10:10pm ET first pitch on the West
    Coast routinely runs past that, so between 1am and 5am ET we also look at
    the previous date. Both fetches hit the schedule cache."""
    now = datetime.now(ET)
    today = today_et()
    dates = [today]
    if 1 <= now.hour < 5:
        dates.insert(0, today - timedelta(days=1))
    live: list[dict] = []
    for d in dates:
        data = fetch_schedule(d.strftime("%Y-%m-%d"))
        live.extend(
            g
            for block in data.get("dates", [])
            for g in block.get("games", [])
            if g["status"]["abstractGameState"] == "Live"
        )
    return live


def game_time_label(game: dict, tz: ZoneInfo | None = None) -> str:
    """First-pitch time in the viewer's zone, or 'TBD' when MLB hasn't set one.
    MLB fills unset times with a sentinel (07:33Z) that would otherwise print as
    a real-looking '3:33 AM', so check the flag before formatting."""
    if (game.get("status") or {}).get("startTimeTBD"):
        return "TBD"
    return fmt_game_time(game.get("gameDate", ""), tz)


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


# First season the current franchise (by today's teamId) played, used to bound
# the random-history picker. Pre-1901 franchises are floored at 1901, where MLB
# statsapi boxscore coverage becomes reliable. Continuity follows the franchise,
# not the city: BAL traces to the 1901 Milwaukee Brewers/St. Louis Browns, etc.
FRANCHISE_FIRST_SEASON = {
    "ARI": 1998, "ATL": 1901, "BAL": 1901, "BOS": 1901, "CHC": 1901,
    "CWS": 1901, "CIN": 1901, "CLE": 1901, "COL": 1993, "DET": 1901,
    "HOU": 1962, "KC":  1969, "LAA": 1961, "LAD": 1901, "MIA": 1993,
    "MIL": 1969, "MIN": 1901, "NYM": 1962, "NYY": 1903, "ATH": 1901,
    "PHI": 1901, "PIT": 1901, "SD":  1969, "SF":  1901, "SEA": 1977,
    "STL": 1901, "TB":  1998, "TEX": 1961, "TOR": 1977, "WSH": 1969,
}

# Past-season schedules never change, so cache them: (team_id, year) -> [game].
_season_cache: dict[tuple[int, int], list[dict]] = {}


def _final_games_for_season(team_id: int, year: int) -> list[dict]:
    """Completed regular-season + postseason games for a team in a season (cached
    for past years). gameType R,F,D,L,W = regular, wild card, division series,
    LCS, World Series — deliberately excludes spring (S), which the API also
    reports as Final."""
    key = (team_id, year)
    if year < today_et().year and key in _season_cache:
        return _season_cache[key]
    try:
        resp = requests.get(
            f"{MLB_API}/schedule",
            params={"sportId": 1, "teamId": team_id, "season": year, "gameType": "R,F,D,L,W"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException:
        return []
    finals = [
        g
        for block in data.get("dates", [])
        for g in block.get("games", [])
        if g.get("status", {}).get("abstractGameState") == "Final"
    ]
    if year < today_et().year:
        _season_cache[key] = finals
    return finals


def random_recap_date(team_abv: str) -> str | None:
    """Pick the date of a random completed game — regular season or postseason —
    from the team's history. Returns 'YYYY-MM-DD' (the game's official date) or
    None if no game could be found (unknown team or repeated API failures)."""
    abv = team_abv.upper()
    if abv not in TEAMS:
        return None
    team_id = TEAMS[abv][0]
    years = list(range(FRANCHISE_FIRST_SEASON.get(abv, 1901), today_et().year + 1))
    random.shuffle(years)
    for year in years[:6]:        # cap API attempts; a season almost always has games
        finals = _final_games_for_season(team_id, year)
        if finals:
            game = random.choice(finals)
            return game.get("officialDate") or game.get("gameDate", "")[:10]
    return None


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
    away_team = game["teams"]["away"]["team"]
    home_team = game["teams"]["home"]["team"]
    away_abv  = team_label(away_team)
    home_abv  = team_label(home_team)
    any_placeholder = is_placeholder_team(away_team) or is_placeholder_team(home_team)

    status    = game["status"]["detailedState"]
    abstract  = game["status"]["abstractGameState"]
    reason    = game["status"].get("reason") or ""

    away_score = game["teams"]["away"].get("score")
    home_score = game["teams"]["home"].get("score")

    linescore   = game.get("linescore", {})
    inning      = linescore.get("currentInning")
    inning_half = linescore.get("inningHalf", "")

    detail_lower = status.lower()
    is_no_play = (
        "postponed" in detail_lower
        or "cancel" in detail_lower
        or "suspended" in detail_lower
    )

    game_time = ""
    if abstract == "Preview" and not is_no_play:
        game_time = game_time_label(game, tz)

    def team_str(team: dict, abv: str) -> str:
        if is_placeholder_team(team):
            return f"{GRAY}{'TBD':<3}{RESET}"
        return fmt_team(abv)

    away_str = team_str(away_team, away_abv)
    home_str = team_str(home_team, home_abv)

    if is_no_play:
        a_sc = f"{GRAY}{fmt_score(away_score)}{RESET}"
        h_sc = f"{GRAY}{fmt_score(home_score)}{RESET}"
        label = status
        if reason and reason.lower() not in detail_lower:
            label = f"{status}: {reason}"
        state = f"{BOLD}{YELLOW}{label}{RESET}"
    elif abstract == "Final":
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

    notes = [game_tag(game)]
    if abstract == "Preview" and game.get("description"):
        notes.append(game["description"])          # "Makeup of 9/26 PPD", "Moved from 9/27"
    note = " · ".join(n for n in notes if n)
    suffix = f"  {GRAY}{note}{RESET}" if note else ""
    if dist_label:
        suffix += f"   {dist_label}"
    print(f"  {away_str} {a_sc}  {DIM}@{RESET}  {home_str} {h_sc}   {state}{suffix}", file=out)

    if any_placeholder:
        # Bracket slot not filled yet: say who the slot is for.
        print(f"         {GRAY}{away_team.get('name', 'TBD')}  @  {home_team.get('name', 'TBD')}{RESET}", file=out)
    elif abstract == "Preview" and not is_no_play:
        away_pp = game["teams"]["away"].get("probablePitcher")
        home_pp = game["teams"]["home"].get("probablePitcher")
        if away_pp or home_pp:
            a = _fmt_pitcher(away_pp or {})
            h = _fmt_pitcher(home_pp or {})
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

    away_abv = team_label(game["teams"]["away"]["team"])
    home_abv = team_label(game["teams"]["home"]["team"])

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


def render_team_recap(date_str: str, team_abv: str, out=None, tz: ZoneInfo | None = None,
                      extra_per_game=None) -> str:
    """Score line + boxscore for each of a team's games on a given date. Reusable by /box/{team}/{date}.

    `extra_per_game(game) -> str` is called after each boxscore and its return value
    is appended verbatim (used by /box to inject the WP sparkline)."""
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
        if extra_per_game:
            extra = extra_per_game(game)
            if extra:
                print(extra, file=_out, end="")

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


def _last_ten(t: dict) -> str:
    for s in t.get("records", {}).get("splitRecords", []):
        if s.get("type") == "lastTen":
            return f"{s.get('wins', 0)}-{s.get('losses', 0)}"
    return "-"


def _run_diff(t: dict) -> str:
    rd = t.get("runDifferential")
    if rd is None:
        return "-"
    return f"+{rd}" if rd > 0 else str(rd)


LEAGUE_NAMES = {103: "American League", 104: "National League"}


def race_number(v) -> str:
    """Magic/elimination numbers: MLB omits the key entirely when it doesn't apply
    (a trailing team has no magicNumber) and sends '-' when it applies but is moot."""
    if v in (None, "", "-"):
        return "-"
    return str(v)


def _has_race_numbers(data: dict) -> bool:
    """True once MLB publishes magic/elimination numbers. They're absent in the
    early season and the offseason, when the columns would be all dashes."""
    return any(
        race_number(t.get("magicNumber")) != "-"
        or race_number(t.get("eliminationNumber")) != "-"
        for record in data.get("records", [])
        for t in record.get("teamRecords", [])
    )


def render_standings(out=None) -> str:
    buf = io.StringIO()
    _out = out or buf

    def p(s=""):
        print(s, file=_out)

    data = fetch_standings()
    show_race    = _has_race_numbers(data)
    letters_used: set[str] = set()

    p()
    p(f"  {BOLD}{CYAN}MLB Standings{RESET}")
    p(f"  {GRAY}{'─' * (66 if show_race else 60)}{RESET}")

    for record in data.get("records", []):
        div = record.get("division", {}).get("name", "Unknown Division")
        p(f"\n  {BOLD}{YELLOW}{div}{RESET}")
        header = f"  {GRAY}{'Team':<22} {'W':>3} {'L':>3} {'PCT':>5} {'GB':>5} {'L10':>5} {'RDIF':>5}"
        if show_race:
            header += f" {'M#':>4} {'E#':>4}"
        p(header + RESET)

        teams = sorted(record.get("teamRecords", []), key=lambda x: rank_key(x, "divisionRank"))
        for i, t in enumerate(teams):
            team_id = t["team"]["id"]
            abv     = abv_from_id(team_id)
            name    = TEAMS.get(abv, (None, t["team"]["name"], WHITE))[1]
            wins    = t.get("wins", 0)
            losses  = t.get("losses", 0)
            pct     = t.get("winningPercentage", ".000")
            gb      = t.get("gamesBack", "-")
            l10     = _last_ten(t)
            rdif    = _run_diff(t)
            color   = team_color(abv)
            marker  = f"{BOLD}{color}" if i == 0 else RESET
            row = f"  {marker}{name:<22}{RESET} {wins:>3} {losses:>3} {pct:>5} {gb:>5} {l10:>5} {rdif:>5}"
            if show_race:
                row += f" {race_number(t.get('magicNumber')):>4} {race_number(t.get('eliminationNumber')):>4}"
            letter = clinch_letter(t)
            if letter:
                letters_used.add(letter)
                row += f" {BOLD}{GREEN}{letter}{RESET}"
            p(row)

    if show_race:
        p()
        p(f"  {GRAY}M# = magic number · E# = elimination number{RESET}")
        if letters_used:
            legend = " · ".join(f"{k} = {v}" for k, v in CLINCH_LABELS.items() if k in letters_used)
            p(f"  {GRAY}{legend}{RESET}")
            p(f"  {GRAY}bracket: curl mlbsched.run/postseason{RESET}")

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


def build_standings_json() -> dict:
    data = fetch_standings()
    divisions = []
    for record in data.get("records", []):
        league_id = (record.get("league") or {}).get("id")
        teams = sorted(record.get("teamRecords", []), key=lambda x: rank_key(x, "divisionRank"))
        rows = []
        for t in teams:
            abv = abv_from_id(t["team"]["id"])
            rows.append({
                "team":            abv,
                "rank":            int(t.get("divisionRank") or 0) or None,
                "name":            TEAMS.get(abv, (None, t["team"]["name"], None))[1],
                "wins":            t.get("wins", 0),
                "losses":          t.get("losses", 0),
                "pct":             t.get("winningPercentage", ".000"),
                "gb":              t.get("gamesBack", "-"),
                "l10":             _last_ten(t),
                "run_diff":        t.get("runDifferential"),
                "division_leader": t.get("divisionLeader", False),
                "magic":           race_number(t.get("magicNumber")),
                "elim":            race_number(t.get("eliminationNumber")),
                "clinched":        t.get("clinched", False),
                "clinch":          clinch_letter(t) or None,
            })
        divisions.append({
            "division": (record.get("division") or {}).get("name", "Unknown Division"),
            "league":   LEAGUE_NAMES.get(league_id),
            "teams":    rows,
        })
    return {"divisions": divisions}


def build_team_list_json() -> dict:
    return {
        "teams": [
            {"team": abv, "id": TEAMS[abv][0], "name": TEAMS[abv][1]}
            for abv in sorted(TEAMS)
        ]
    }


def build_box_json(team_abv: str, date_str: str) -> dict:
    """JSON counterpart to render_team_recap/render_boxscore: line score for a team's games on a date.

    Uses only the already-hydrated `linescore` from fetch_schedule, so this costs no
    extra upstream calls. Win probability is deliberately left to /api/wp — folding it
    in here would mean a fetch_wp() per game."""
    abv = team_abv.upper()
    if abv not in TEAMS:
        return {"error": f"Unknown team: {abv}"}

    team_id = TEAMS[abv][0]
    data    = fetch_schedule(date_str, team_id)

    def side_totals(side: dict) -> dict:
        # No defaults: a scheduled game reports no runs at all, which is not the same as 0.
        return {
            "runs":   side.get("runs"),
            "hits":   side.get("hits"),
            "errors": side.get("errors"),
        }

    games_out: list[dict] = []
    for block in data.get("dates", []):
        for game in block.get("games", []):
            away_team = game["teams"]["away"]["team"]
            home_team = game["teams"]["home"]["team"]
            away_abv = team_label(away_team)
            home_abv = team_label(home_team)
            status   = game["status"]["abstractGameState"]

            ls      = game.get("linescore", {})
            totals  = ls.get("teams", {})
            away_t  = side_totals(totals.get("away", {}))
            home_t  = side_totals(totals.get("home", {}))

            innings = [
                {
                    "num":  inn.get("num"),
                    "away": (inn.get("away") or {}).get("runs"),
                    "home": (inn.get("home") or {}).get("runs"),
                }
                for inn in ls.get("innings", [])
            ]

            # Only a Final game has a winner; a tie or suspended game reports none.
            winner = None
            if status == "Final" and away_t["runs"] is not None and home_t["runs"] is not None:
                if away_t["runs"] > home_t["runs"]:
                    winner = away_abv
                elif home_t["runs"] > away_t["runs"]:
                    winner = home_abv

            loc = game_location(game)

            games_out.append({
                "game_pk":    game.get("gamePk"),
                "away":       None if is_placeholder_team(away_team) else away_abv,
                "away_name":  TEAMS.get(away_abv, (None, away_team.get("name", away_abv), None))[1],
                "home":       None if is_placeholder_team(home_team) else home_abv,
                "home_name":  TEAMS.get(home_abv, (None, home_team.get("name", home_abv), None))[1],
                "away_score": game["teams"]["away"].get("score"),
                "home_score": game["teams"]["home"].get("score"),
                "status":     status,
                "detail":     game["status"]["detailedState"],
                "reason":     game["status"].get("reason") or None,
                # UTC as MLB reports it — no viewer-timezone personalization, unlike the HTML routes.
                "game_date":  game.get("gameDate") or None,
                "start_time_tbd": bool(game["status"].get("startTimeTBD")),
                "game_type":  game.get("gameType"),
                "series":     series_tag(game) or None,
                "doubleheader": is_doubleheader(game),
                "game_number": game.get("gameNumber"),
                "description": game.get("description") or None,
                "venue":      loc[0] if loc else None,
                "innings":    innings,
                "totals":     {"away": away_t, "home": home_t},
                "winner":     winner,
            })

    return {"team": abv, "date": date_str, "games": games_out}


def render_live(out=None, tz: ZoneInfo | None = None) -> str:
    buf = io.StringIO()
    _out = out or buf

    def p(s=""):
        print(s, file=_out)

    today = today_et()
    live_games = live_games_now()

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
    live_games = live_games_now()          # includes last night's late games before 5am ET
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
            render_no_games_note(today, _out)
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
    curl mlbsched.run/<TEAM>               Team's game today        (e.g. NYM)
    curl mlbsched.run/<TEAM>/<DATE>        Team on a specific date
    curl mlbsched.run/<TEAM>/next          Team's next game, with a countdown
    curl mlbsched.run/<DATE>               Full schedule on a date  (YYYY-MM-DD)
    curl mlbsched.run/yesterday            Yesterday's scores
    curl mlbsched.run/yesterday/<TEAM>     Team yesterday
    curl mlbsched.run/tomorrow             Tomorrow's schedule
    curl mlbsched.run/tomorrow/<TEAM>      Team tomorrow
    curl mlbsched.run/live                 All games in progress right now
    curl mlbsched.run/box/<TEAM>           Yesterday's boxscore for a team
    curl mlbsched.run/box/<TEAM>/<DATE>    Boxscore for a team on a specific date
    curl mlbsched.run/box/<TEAM>/random    Boxscore from a random game in history
    curl mlbsched.run/distance             Today's games sorted by distance from you
    curl mlbsched.run/odds                 Today's odds — best NY sportsbook price per market
    curl mlbsched.run/odds/<TEAM>          Odds for one team's game today
    curl mlbsched.run/bestbets             Pricing edges vs the multi-book no-vig consensus
    curl mlbsched.run/bestbets/<TEAM>      Edges for one team's game today
    curl mlbsched.run/weather              Current weather at each stadium
    curl mlbsched.run/standings            Division standings (W-L, PCT, GB, L10, run diff)
    curl mlbsched.run/wildcard             Wild Card race per league
    curl mlbsched.run/postseason           Playoff bracket — series records, live games, what's next
    curl mlbsched.run/postseason/<YEAR>    A past postseason (e.g. 2015)
    curl mlbsched.run/h2h/<TEAM>/<TEAM>    Season head-to-head series
    curl mlbsched.run/player/<NAME>        Player season stats + last game (e.g. lindor)
    curl mlbsched.run/lineup/<TEAM>        Today's batting order for a team's game
    curl mlbsched.run/pitchers             Today's probable starting-pitcher matchups
    curl mlbsched.run/streaks              Teams on hot or cold runs (4+ games, ?min=N)
    curl mlbsched.run/leaders              Top batting + pitching leaders (HR, AVG, OPS, W, ERA, K)
    curl mlbsched.run/leaders/<STAT>       Top 25 in one stat (e.g. ops, era, whip, hr, sb)
    curl mlbsched.run/broadcasts           TV broadcasts for today's games
    curl mlbsched.run/broadcasts/<TEAM>    TV broadcasts for one team today
    curl mlbsched.run/ical                 Subscribe to a team's schedule (calendar feed)
    curl mlbsched.run/ical/<TEAM>.ics      iCal feed of a team's full season
    curl mlbsched.run/teams                All team abbreviations
    curl mlbsched.run/about                What this is, how it's built, who made it
    curl mlbsched.run/random               Random MLB mascot ASCII art
    curl mlbsched.run/today                Today's schedule only (minimal scoreboard)
    curl mlbsched.run/onthisday            On this date in MLB history (10/25/50 years ago)
    curl mlbsched.run/birthdays            Active players born on today's date
    curl mlbsched.run/birthdays/all        All-time legends born on today's date
    curl mlbsched.run/wp/<TEAM>            Win-probability sparkline for the team's last completed game
    curl mlbsched.run/wp/<TEAM>/<DATE>     Win-probability sparkline for a specific date

  {BOLD}Examples:{RESET}
    curl mlbsched.run
    curl mlbsched.run/NYM
    curl mlbsched.run/NYM/1962-04-13          # first Mets home game, Polo Grounds
    curl mlbsched.run/standings

  {GRAY}by Brian Pisano — brianpisano.com{RESET}
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

    if first == "about":
        import about
        about.render_about(out=sys.stdout)
        return

    if first == "teams":
        render_team_list(out=sys.stdout)
        return

    if first == "standings":
        render_standings(out=sys.stdout)
        return

    if first == "postseason":
        import postseason
        year = int(args[1]) if len(args) > 1 and args[1].isdigit() else None
        postseason.render_postseason(year, out=sys.stdout)
        return

    if first == "wildcard":
        import wildcard
        wildcard.render_wildcard(out=sys.stdout)
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
