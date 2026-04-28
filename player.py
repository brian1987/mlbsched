"""player — player lookup with season + last-game stats"""

import io
import time
from threading import Lock

import requests

from mlbsched import (
    BOLD, DIM, RESET, RED, YELLOW, CYAN, WHITE, GRAY,
    TEAMS, MLB_API, abv_from_id, team_color, today_et,
)

# 1-hour TTL cache for the all-players roster
_PLAYERS_CACHE: dict[int, list[dict]] = {}
_PLAYERS_AT:    dict[int, float]      = {}
_PLAYERS_LOCK = Lock()
_TTL = 3600


def fetch_all_players(season: int) -> list[dict]:
    with _PLAYERS_LOCK:
        if season in _PLAYERS_CACHE and time.time() - _PLAYERS_AT.get(season, 0) < _TTL:
            return _PLAYERS_CACHE[season]
    try:
        resp = requests.get(
            f"{MLB_API}/sports/1/players",
            params={"season": season},
            timeout=10,
        )
        resp.raise_for_status()
    except requests.RequestException:
        return _PLAYERS_CACHE.get(season, [])

    data = resp.json()
    out: list[dict] = []
    for p in data.get("people", []):
        team_id = (p.get("currentTeam") or {}).get("id")
        out.append({
            "id":       p.get("id"),
            "fullName": p.get("fullName", ""),
            "position": (p.get("primaryPosition") or {}).get("abbreviation", ""),
            "team_id":  team_id,
            "team_abv": abv_from_id(team_id) if team_id else "FA",
        })
    with _PLAYERS_LOCK:
        _PLAYERS_CACHE[season] = out
        _PLAYERS_AT[season]    = time.time()
    return out


def find_player(query: str, season: int) -> tuple[list[dict], dict | None]:
    """Returns (all_matches, best_or_None)."""
    q = (query or "").lower().strip().replace("-", " ").replace("_", " ")
    if not q:
        return [], None
    players = fetch_all_players(season)

    exacts = [p for p in players if p["fullName"].lower() == q]
    if exacts:
        return exacts, exacts[0]

    matches = [p for p in players if q in p["fullName"].lower()]
    if len(matches) == 1:
        return matches, matches[0]
    return matches, None


def fetch_player_stats(player_id: int, season: int) -> dict:
    out = {
        "id":        player_id,
        "name":      "",
        "position":  "",
        "team_id":   None,
        "team_abv":  "??",
        "season":    {},   # group → stat dict
        "last_game": {},   # group → split (whole split, not just stat)
    }
    try:
        resp = requests.get(
            f"{MLB_API}/people/{player_id}",
            params={
                "hydrate": (
                    f"currentTeam,"
                    f"stats(group=[hitting,pitching],type=[season,gameLog],season={season})"
                ),
            },
            timeout=10,
        )
        resp.raise_for_status()
    except requests.RequestException:
        return out

    people = resp.json().get("people", [])
    if not people:
        return out
    p = people[0]
    out["name"]     = p.get("fullName", "")
    out["position"] = (p.get("primaryPosition") or {}).get("abbreviation", "")
    team_id = (p.get("currentTeam") or {}).get("id")
    out["team_id"]  = team_id
    out["team_abv"] = abv_from_id(team_id) if team_id else "FA"

    for block in p.get("stats", []):
        group = (block.get("group") or {}).get("displayName", "").lower()  # hitting/pitching
        ttype = (block.get("type")  or {}).get("displayName", "").lower()  # season/gameLog
        splits = block.get("splits") or []
        if not splits:
            continue
        if ttype == "season":
            out["season"][group] = splits[0].get("stat", {})
        elif ttype == "gamelog":
            out["last_game"][group] = splits[-1]
    return out


# ── Rendering ─────────────────────────────────────────────────────────────────

def _strip_zero(val) -> str:
    s = str(val)
    return s[1:] if s.startswith("0.") else s


def _hitting_season_line(s: dict) -> str:
    cols = [
        ("G",   s.get("gamesPlayed", "-"),        3),
        ("AB",  s.get("atBats", "-"),             4),
        ("R",   s.get("runs", "-"),               3),
        ("H",   s.get("hits", "-"),               3),
        ("HR",  s.get("homeRuns", "-"),           3),
        ("RBI", s.get("rbi", "-"),                4),
        ("SB",  s.get("stolenBases", "-"),        3),
        ("BB",  s.get("baseOnBalls", "-"),        3),
        ("SO",  s.get("strikeOuts", "-"),         3),
        ("AVG", _strip_zero(s.get("avg", "-")),   5),
        ("OBP", _strip_zero(s.get("obp", "-")),   5),
        ("SLG", _strip_zero(s.get("slg", "-")),   5),
        ("OPS", _strip_zero(s.get("ops", "-")),   5),
    ]
    return cols


def _pitching_season_line(s: dict) -> str:
    cols = [
        ("G",    s.get("gamesPlayed", "-"),         3),
        ("W",    s.get("wins", "-"),                3),
        ("L",    s.get("losses", "-"),              3),
        ("SV",   s.get("saves", "-"),               3),
        ("IP",   s.get("inningsPitched", "-"),      6),
        ("H",    s.get("hits", "-"),                4),
        ("ER",   s.get("earnedRuns", "-"),          4),
        ("BB",   s.get("baseOnBalls", "-"),         4),
        ("K",    s.get("strikeOuts", "-"),          4),
        ("ERA",  s.get("era", "-"),                 5),
        ("WHIP", s.get("whip", "-"),                5),
    ]
    return cols


def _print_table(cols, label, color, out) -> None:
    headers = " ".join(f"{GRAY}{h:>{w}}{RESET}" for h, _v, w in cols)
    values  = " ".join(f"{color}{str(v):>{w}}{RESET}" for _h, v, w in cols)
    print(f"  {BOLD}{label}{RESET}", file=out)
    print(f"   {headers}", file=out)
    print(f"   {values}",  file=out)


def _hitting_last_line(split: dict) -> str:
    s = split.get("stat", {})
    ab   = s.get("atBats", 0)
    h    = s.get("hits", 0)
    hr   = s.get("homeRuns", 0)
    rbi  = s.get("rbi", 0)
    runs = s.get("runs", 0)
    bb   = s.get("baseOnBalls", 0)
    so   = s.get("strikeOuts", 0)

    parts = [f"{h}-for-{ab}"]
    if hr:  parts.append(f"{hr} HR")
    if rbi: parts.append(f"{rbi} RBI")
    if runs: parts.append(f"{runs} R")
    if bb:  parts.append(f"{bb} BB")
    if so:  parts.append(f"{so} K")
    return ", ".join(parts)


def _pitching_last_line(split: dict) -> str:
    s = split.get("stat", {})
    ip = s.get("inningsPitched", "0.0")
    hits = s.get("hits", 0)
    er = s.get("earnedRuns", 0)
    bb = s.get("baseOnBalls", 0)
    k  = s.get("strikeOuts", 0)
    decision = []
    if s.get("wins"):   decision.append("W")
    if s.get("losses"): decision.append("L")
    if s.get("saves"):  decision.append("SV")
    if s.get("holds"):  decision.append("HLD")
    parts = [f"{ip} IP", f"{hits} H", f"{er} ER", f"{bb} BB", f"{k} K"]
    out = ", ".join(parts)
    if decision:
        out = f"({'/'.join(decision)})  " + out
    return out


def _opponent_label(split: dict) -> str:
    opp = split.get("opponent") or {}
    opp_abv = abv_from_id(opp.get("id")) if opp.get("id") else "???"
    is_home = split.get("isHome", False)
    sep = "vs" if is_home else "@"
    date = split.get("date", "")
    return f"{sep} {opp_abv}  {GRAY}({date}){RESET}"


def render_player(query: str, out=None) -> str:
    buf = io.StringIO()
    _out = out or buf

    def p(s=""):
        print(s, file=_out)

    season = today_et().year
    matches, best = find_player(query, season)

    if not matches:
        p()
        p(f"  {RED}No player found matching: {query}{RESET}")
        p(f"  {GRAY}Try a longer name fragment, e.g. 'judge' or 'aaron judge'.{RESET}")
        p()
        return buf.getvalue()

    if best is None:
        p()
        p(f"  {YELLOW}Multiple players match '{query}':{RESET}")
        for m in matches[:25]:
            color = team_color(m["team_abv"])
            p(f"    {BOLD}{color}{m['team_abv']:<3}{RESET}  {GRAY}{m['position']:<3}{RESET}  {m['fullName']}")
        if len(matches) > 25:
            p(f"    {GRAY}... and {len(matches) - 25} more{RESET}")
        p()
        return buf.getvalue()

    stats = fetch_player_stats(best["id"], season)
    name     = stats["name"] or best["fullName"]
    pos      = stats["position"] or best["position"]
    team_abv = stats["team_abv"]
    color    = team_color(team_abv)

    p()
    p(f"  {BOLD}{CYAN}{name}{RESET}  {GRAY}—{RESET}  "
      f"{BOLD}{color}{team_abv}{RESET} {GRAY}({pos}){RESET}  "
      f"{GRAY}{season} season{RESET}")
    p(f"  {GRAY}{'─' * 64}{RESET}")

    has_hitting  = bool(stats["season"].get("hitting"))
    has_pitching = bool(stats["season"].get("pitching"))

    if not has_hitting and not has_pitching:
        p(f"  {GRAY}No stats yet this season.{RESET}")
        p()
        return buf.getvalue()

    if has_hitting:
        cols = _hitting_season_line(stats["season"]["hitting"])
        _print_table(cols, "Hitting", color, _out)
        last = stats["last_game"].get("hitting")
        if last:
            p(f"   {GRAY}Last game{RESET}  {_opponent_label(last)}  {WHITE}{_hitting_last_line(last)}{RESET}")
        if has_pitching:
            p()

    if has_pitching:
        cols = _pitching_season_line(stats["season"]["pitching"])
        _print_table(cols, "Pitching", color, _out)
        last = stats["last_game"].get("pitching")
        if last:
            p(f"   {GRAY}Last game{RESET}  {_opponent_label(last)}  {WHITE}{_pitching_last_line(last)}{RESET}")

    p()
    return buf.getvalue()


def build_player_json(query: str) -> dict:
    season = today_et().year
    matches, best = find_player(query, season)
    if not matches:
        return {"query": query, "matches": [], "player": None}

    if best is None:
        return {
            "query":   query,
            "matches": [
                {"id": m["id"], "name": m["fullName"], "team": m["team_abv"], "position": m["position"]}
                for m in matches
            ],
            "player":  None,
        }

    stats = fetch_player_stats(best["id"], season)

    def _last(group: str) -> dict | None:
        split = stats["last_game"].get(group)
        if not split:
            return None
        opp = split.get("opponent") or {}
        return {
            "date":     split.get("date"),
            "opponent": abv_from_id(opp.get("id")) if opp.get("id") else None,
            "is_home":  split.get("isHome", False),
            "stat":     split.get("stat", {}),
        }

    return {
        "query":     query,
        "player": {
            "id":        best["id"],
            "name":      stats["name"],
            "position":  stats["position"],
            "team":      stats["team_abv"],
            "season":    season,
            "hitting":   stats["season"].get("hitting"),
            "pitching":  stats["season"].get("pitching"),
            "last_hitting":  _last("hitting"),
            "last_pitching": _last("pitching"),
        },
    }
