"""wildcard — playoff race per league"""

import io

from mlbsched import (
    BOLD, DIM, RESET, YELLOW, CYAN, WHITE, GRAY,
    TEAMS, abv_from_id, team_color, fetch_standings,
)

LEAGUES = {
    103: "American League",
    104: "National League",
}


def _pct(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def _team_summary(t: dict) -> dict:
    team_id  = t["team"]["id"]
    abv      = abv_from_id(team_id)
    league   = t["team"].get("league", {}) or {}
    division = t["team"].get("division", {}) or {}
    return {
        "abv":             abv,
        "team_id":         team_id,
        "name":            TEAMS.get(abv, (None, t["team"].get("name", abv), None))[1],
        "league_id":       league.get("id"),
        "league_name":     league.get("name"),
        "division":        division.get("name", ""),
        "wins":            t.get("wins", 0),
        "losses":          t.get("losses", 0),
        "pct":             t.get("winningPercentage", ".000"),
        "gb":              t.get("gamesBack", "-"),
        "wc_gb":           t.get("wildCardGamesBack", "-"),
        "division_leader": t.get("divisionLeader", False),
        "elim":            t.get("eliminationNumber", "-"),
        "wc_elim":         t.get("wildCardEliminationNumber", "-"),
        "magic":           t.get("magicNumber"),
        "clinched":        t.get("clinched", False),
    }


def get_wildcard() -> dict:
    """Returns {103: {leaders, wildcard, chasing}, 104: {...}}"""
    data = fetch_standings()
    by_league: dict[int, list[dict]] = {103: [], 104: []}
    for record in data.get("records", []):
        for t in record.get("teamRecords", []):
            r = _team_summary(t)
            if r["league_id"] in by_league:
                by_league[r["league_id"]].append(r)

    out: dict[int, dict] = {}
    for league_id, teams in by_league.items():
        leaders     = [t for t in teams if t["division_leader"]]
        non_leaders = [t for t in teams if not t["division_leader"]]
        leaders.sort(key=lambda x: -_pct(x["pct"]))
        non_leaders.sort(key=lambda x: -_pct(x["pct"]))
        out[league_id] = {
            "leaders":  leaders,
            "wildcard": non_leaders[:3],
            "chasing":  non_leaders[3:],
        }
    return out


def _short_division(name: str) -> str:
    return (
        name.replace("American League ", "AL ")
            .replace("National League ", "NL ")
    )


def _wc_row(rank: int, t: dict, in_line: bool, out) -> None:
    abv    = t["abv"]
    name   = t["name"]
    color  = team_color(abv)
    record = f"{t['wins']}-{t['losses']}"
    pct    = t["pct"]
    wc_gb  = t.get("wc_gb") or "-"
    abv_marker  = f"{BOLD}{color}" if in_line else f"{color}"
    name_marker = f"{BOLD}{WHITE}" if in_line else RESET
    print(
        f"   {GRAY}{rank:>2}{RESET}  "
        f"{abv_marker}{abv:<3}{RESET}  "
        f"{name_marker}{name:<22}{RESET}  "
        f"{record:>7}  "
        f"{pct:>5}  "
        f"{wc_gb:>5}",
        file=out,
    )


def render_wildcard(out=None) -> str:
    buf = io.StringIO()
    _out = out or buf

    def p(s=""):
        print(s, file=_out)

    races = get_wildcard()

    p()
    p(f"  {BOLD}{CYAN}Wild Card Race{RESET}")
    p(f"  {GRAY}{'─' * 60}{RESET}")

    for league_id in (103, 104):
        race = races.get(league_id) or {}
        p(f"\n  {BOLD}{YELLOW}{LEAGUES[league_id]}{RESET}")

        # Division leaders (auto-qualify)
        p(f"  {GRAY}Division Leaders{RESET}")
        for t in race.get("leaders", []):
            abv     = t["abv"]
            color   = team_color(abv)
            record  = f"{t['wins']}-{t['losses']}"
            div     = _short_division(t["division"])
            print(
                f"       {BOLD}{color}{abv:<3}{RESET}  "
                f"{GRAY}{div:<10}{RESET}  "
                f"{record:>7}  {t['pct']:>5}",
                file=_out,
            )

        # Wild Card race
        p(f"\n  {GRAY}Wild Card{RESET}")
        p(f"   {GRAY}{'#':>2}  {'':<3}  {'Team':<22}  {'Record':>7}  {'PCT':>5}  {'GB':>5}{RESET}")
        wc = race.get("wildcard", [])
        for i, t in enumerate(wc, start=1):
            _wc_row(i, t, in_line=True, out=_out)

        chasing = race.get("chasing", [])
        if chasing and wc:
            p(f"   {GRAY}{'─' * 56}{RESET}")
        for i, t in enumerate(chasing, start=len(wc) + 1):
            _wc_row(i, t, in_line=False, out=_out)

    p()
    return buf.getvalue()


def build_team_json(t: dict) -> dict:
    return {
        "team":            t["abv"],
        "name":            t["name"],
        "wins":            t["wins"],
        "losses":          t["losses"],
        "pct":             t["pct"],
        "division":        t["division"],
        "division_leader": t["division_leader"],
        "gb":              t["gb"],
        "wc_gb":           t["wc_gb"],
        "elim":            t["elim"],
        "wc_elim":         t["wc_elim"],
        "magic":           t["magic"],
        "clinched":        t["clinched"],
    }


def get_wildcard_json() -> dict:
    races = get_wildcard()
    return {
        league_code: {
            "league":   LEAGUES[league_id],
            "leaders":  [build_team_json(t) for t in race.get("leaders", [])],
            "wildcard": [build_team_json(t) for t in race.get("wildcard", [])],
            "chasing":  [build_team_json(t) for t in race.get("chasing", [])],
        }
        for league_id, league_code, race in (
            (103, "AL", races.get(103, {})),
            (104, "NL", races.get(104, {})),
        )
    }
