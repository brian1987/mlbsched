"""leaders — MLB stat leaders (batting + pitching)"""

import io
from concurrent.futures import ThreadPoolExecutor

import requests

from mlbsched import (
    BOLD, DIM, RESET, RED, YELLOW, CYAN, WHITE, GRAY,
    MLB_API, abv_from_id, team_color, today_et,
)

# alias → (MLB API leaderCategory, display label, statGroup, format)
HITTING_STATS: dict[str, tuple[str, str, str, str]] = {
    "avg":  ("battingAverage",       "AVG",  "hitting", "rate3"),
    "obp":  ("onBasePercentage",     "OBP",  "hitting", "rate3"),
    "slg":  ("sluggingPercentage",   "SLG",  "hitting", "rate3"),
    "ops":  ("onBasePlusSlugging",   "OPS",  "hitting", "rate3"),
    "hr":   ("homeRuns",             "HR",   "hitting", "int"),
    "rbi":  ("runsBattedIn",         "RBI",  "hitting", "int"),
    "r":    ("runs",                 "R",    "hitting", "int"),
    "h":    ("hits",                 "H",    "hitting", "int"),
    "2b":   ("doubles",              "2B",   "hitting", "int"),
    "3b":   ("triples",              "3B",   "hitting", "int"),
    "sb":   ("stolenBases",          "SB",   "hitting", "int"),
    "bb":   ("walks",                "BB",   "hitting", "int"),
    "so":   ("strikeOuts",           "SO",   "hitting", "int"),
    "tb":   ("totalBases",           "TB",   "hitting", "int"),
}

PITCHING_STATS: dict[str, tuple[str, str, str, str]] = {
    "era":  ("earnedRunAverage",             "ERA",  "pitching", "passthrough"),
    "w":    ("wins",                         "W",    "pitching", "int"),
    "sv":   ("saves",                        "SV",   "pitching", "int"),
    "k":    ("strikeOuts",                   "K",    "pitching", "int"),
    "whip": ("walksAndHitsPerInningPitched", "WHIP", "pitching", "passthrough"),
    "ip":   ("inningsPitched",               "IP",   "pitching", "passthrough"),
    "kbb":  ("strikeoutWalkRatio",           "K/BB", "pitching", "passthrough"),
    "hld":  ("holds",                        "HLD",  "pitching", "int"),
    "oba":  ("opponentBattingAverage",       "OBA",  "pitching", "rate3"),
}

ALL_STATS: dict[str, tuple[str, str, str, str]] = {**HITTING_STATS, **PITCHING_STATS}

# Curated dashboard: each tile fetches top N for that category
DASHBOARD: list[tuple[str, list[tuple[str, str, int]]]] = [
    ("hitting", [
        ("hr",  "Home Runs",   5),
        ("avg", "Batting Avg", 5),
        ("ops", "OPS",         5),
    ]),
    ("pitching", [
        ("w",   "Wins",       5),
        ("era", "ERA",        5),
        ("k",   "Strikeouts", 5),
    ]),
]


def fetch_leaders(category: str, stat_group: str, limit: int = 10) -> list[dict]:
    """Returns ordered list of {rank, value, person_id, name, abv}."""
    season = today_et().year
    params = {
        "leaderCategories": category,
        "statGroup":        stat_group,
        "season":           season,
        "sportId":          1,
        "limit":            limit,
    }
    try:
        resp = requests.get(f"{MLB_API}/stats/leaders", params=params, timeout=10)
        resp.raise_for_status()
    except requests.RequestException:
        return []

    data = resp.json()
    out: list[dict] = []
    for block in data.get("leagueLeaders", []):
        for L in block.get("leaders", []):
            person  = L.get("person", {}) or {}
            team    = L.get("team", {}) or {}
            team_id = team.get("id")
            abv     = abv_from_id(team_id) if team_id else "???"
            out.append({
                "rank":      L.get("rank"),
                "value":     L.get("value"),
                "person_id": person.get("id"),
                "name":      person.get("fullName", "?"),
                "abv":       abv,
            })
    return out


def _fmt_value(val, fmt: str) -> str:
    if val is None:
        return "-"
    s = str(val)
    if fmt == "rate3" and s.startswith("0."):
        # batting-avg style: ".345" rather than "0.345"
        return s[1:]
    return s


def render_leaders_one(alias: str, count: int = 25, out=None) -> str:
    buf = io.StringIO()
    _out = out or buf

    def p(s=""):
        print(s, file=_out)

    if alias not in ALL_STATS:
        p()
        p(f"  {RED}Unknown stat: {alias}{RESET}")
        p(f"  {GRAY}Hitting : {', '.join(HITTING_STATS.keys())}{RESET}")
        p(f"  {GRAY}Pitching: {', '.join(PITCHING_STATS.keys())}{RESET}")
        p()
        return buf.getvalue()

    cat, label, group, fmt = ALL_STATS[alias]
    rows = fetch_leaders(cat, group, count)

    p()
    p(f"  {BOLD}{CYAN}{label} Leaders{RESET}  {GRAY}({today_et().year} season){RESET}")
    p(f"  {GRAY}{'─' * 52}{RESET}")

    if not rows:
        p(f"  {GRAY}No data available.{RESET}")
        p()
        return buf.getvalue()

    p(f"  {GRAY}{'#':>3}  {'Player':<24} {'Team':<4} {label:>7}{RESET}")
    for r in rows:
        rank = r["rank"]
        name = r["name"]
        abv  = r["abv"]
        val  = _fmt_value(r["value"], fmt)
        team_str = f"{BOLD}{team_color(abv)}{abv:<4}{RESET}"
        marker = f"{BOLD}{WHITE}" if rank == 1 else RESET
        p(f"  {GRAY}{rank:>3}{RESET}  {marker}{name:<24}{RESET} {team_str} {val:>7}")

    p()
    return buf.getvalue()


def render_leaders_dashboard(out=None) -> str:
    buf = io.StringIO()
    _out = out or buf

    def p(s=""):
        print(s, file=_out)

    # fan out the 6 fetches in parallel
    tasks: list[tuple[str, str, str, int, str]] = []  # (group_name, alias, title, n, fmt)
    fetch_args: list[tuple[str, str, int]] = []        # (cat, group, n)
    for group_name, tiles in DASHBOARD:
        for alias, title, n in tiles:
            cat, _label, group, fmt = ALL_STATS[alias]
            tasks.append((group_name, alias, title, n, fmt))
            fetch_args.append((cat, group, n))

    with ThreadPoolExecutor(max_workers=len(fetch_args)) as ex:
        results = list(ex.map(lambda a: fetch_leaders(*a), fetch_args))

    by_alias: dict[str, list[dict]] = {tasks[i][1]: results[i] for i in range(len(tasks))}
    fmt_by_alias: dict[str, str]    = {tasks[i][1]: tasks[i][4] for i in range(len(tasks))}

    p()
    p(f"  {BOLD}{CYAN}MLB Leaders{RESET}  {GRAY}({today_et().year} season){RESET}")
    p(f"  {GRAY}{'─' * 52}{RESET}")

    for group_name, tiles in DASHBOARD:
        section = "Hitting" if group_name == "hitting" else "Pitching"
        p(f"\n  {BOLD}{YELLOW}{section}{RESET}")
        for alias, title, _n in tiles:
            rows = by_alias.get(alias, [])
            fmt  = fmt_by_alias[alias]
            p(f"  {GRAY}{title}{RESET}")
            if not rows:
                p(f"    {GRAY}—{RESET}")
                continue
            for r in rows:
                rank = r["rank"]
                name = r["name"]
                abv  = r["abv"]
                val  = _fmt_value(r["value"], fmt)
                team_str = f"{BOLD}{team_color(abv)}{abv:<3}{RESET}"
                marker = f"{BOLD}{WHITE}" if rank == 1 else RESET
                p(f"    {GRAY}{rank:>2}{RESET} {team_str}  {marker}{name:<22}{RESET} {val:>7}")

    p()
    p(f"  {GRAY}More: curl mlbsched.run/leaders/<stat>  e.g. /leaders/ops, /leaders/whip{RESET}")
    p()
    return buf.getvalue()


def build_leader_json(r: dict) -> dict:
    return {
        "rank":      r["rank"],
        "value":     r["value"],
        "player":    r["name"],
        "player_id": r["person_id"],
        "team":      r["abv"],
    }


def get_leaders(alias: str, count: int = 25) -> dict | None:
    if alias not in ALL_STATS:
        return None
    cat, label, group, _fmt = ALL_STATS[alias]
    rows = fetch_leaders(cat, group, count)
    return {
        "stat":    alias,
        "label":   label,
        "group":   group,
        "season":  today_et().year,
        "leaders": [build_leader_json(r) for r in rows],
    }


def list_stats() -> dict:
    return {
        "hitting":  list(HITTING_STATS.keys()),
        "pitching": list(PITCHING_STATS.keys()),
    }
