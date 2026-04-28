"""lineup — today's batting order for a team's game"""

import io
from zoneinfo import ZoneInfo

import requests

import mlbsched as sched
from mlbsched import (
    BOLD, DIM, RESET, RED, YELLOW, CYAN, WHITE, GRAY,
    TEAMS, MLB_API, abv_from_id, team_color, today_et, fmt_game_time,
)


def fetch_boxscore(game_pk: int) -> dict:
    try:
        resp = requests.get(f"{MLB_API}/game/{game_pk}/boxscore", timeout=10)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException:
        return {}


def _team_games_today(abv: str) -> list[dict]:
    if abv not in TEAMS:
        return []
    team_id = TEAMS[abv][0]
    data = sched.fetch_schedule(today_et().strftime("%Y-%m-%d"), team_id)
    return [g for d in data.get("dates", []) for g in d.get("games", [])]


def _is_no_play(g: dict) -> bool:
    s = (g["status"].get("detailedState") or "").lower()
    return "postponed" in s or "cancel" in s or "suspended" in s


def _fmt_avg(s: dict) -> str:
    avg = s.get("avg", "-")
    return avg[1:] if str(avg).startswith("0.") else str(avg)


def _fmt_ops(s: dict) -> str:
    ops = s.get("ops", "-")
    return ops[1:] if str(ops).startswith("0.") else str(ops)


def _render_team_lineup(side: str, side_label: str, team_box: dict, prob_pitcher: dict | None, color: str, out) -> None:
    abv = team_box["team"].get("abbreviation", "??")
    name = TEAMS.get(abv, (None, abv, None))[1]
    print(file=out)
    print(f"  {BOLD}{color}{name}{RESET}  {GRAY}({side_label}){RESET}", file=out)

    bo = team_box.get("battingOrder") or []
    players = team_box.get("players") or {}

    if not bo:
        print(f"   {GRAY}Lineup not yet posted.{RESET}", file=out)
    else:
        for i, pid in enumerate(bo, start=1):
            p = players.get(f"ID{pid}", {})
            pos = (p.get("position") or {}).get("abbreviation", "")
            person = p.get("person", {})
            full   = person.get("fullName", "?")
            season = (p.get("seasonStats") or {}).get("batting") or {}
            avg = _fmt_avg(season)
            hr  = season.get("homeRuns", "-")
            ops = _fmt_ops(season)
            print(
                f"   {GRAY}{i}.{RESET} "
                f"{GRAY}{pos:<3}{RESET}  "
                f"{full:<22}  "
                f"{GRAY}AVG{RESET} {avg:>5}  "
                f"{GRAY}HR{RESET} {str(hr):>3}  "
                f"{GRAY}OPS{RESET} {ops:>5}",
                file=out,
            )

    if prob_pitcher:
        line = sched._fmt_pitcher(prob_pitcher)
        print(f"   {GRAY}SP{RESET}  {line}", file=out)


def render_lineup(team_abv: str, tz: ZoneInfo | None = None, out=None) -> str:
    buf = io.StringIO()
    _out = out or buf

    def p(s=""):
        print(s, file=_out)

    abv = team_abv.upper()
    if abv not in TEAMS:
        p()
        p(f"  {RED}Unknown team: {abv}{RESET}")
        p(f"  {GRAY}Try: curl mlbsched.run/teams{RESET}")
        p()
        return buf.getvalue()

    games = _team_games_today(abv)
    if not games:
        p()
        p(f"  {GRAY}{TEAMS[abv][1]} have no game today.{RESET}")
        p()
        return buf.getvalue()

    # If multiple games (DH), pick the first one not yet final
    game = next((g for g in games if g["status"]["abstractGameState"] != "Final"), games[0])

    if _is_no_play(game):
        p()
        p(f"  {YELLOW}{TEAMS[abv][1]} game today: {game['status']['detailedState']}{RESET}")
        p()
        return buf.getvalue()

    away_abv = abv_from_id(game["teams"]["away"]["team"]["id"])
    home_abv = abv_from_id(game["teams"]["home"]["team"]["id"])
    away_pp  = game["teams"]["away"].get("probablePitcher")
    home_pp  = game["teams"]["home"].get("probablePitcher")

    box = fetch_boxscore(game["gamePk"])
    if not box:
        p()
        p(f"  {RED}Could not fetch lineup data.{RESET}")
        p()
        return buf.getvalue()

    game_time = fmt_game_time(game.get("gameDate", ""), tz)
    status    = game["status"].get("detailedState", "")

    p()
    p(f"  {BOLD}{CYAN}Lineups{RESET}  "
      f"{BOLD}{team_color(away_abv)}{away_abv}{RESET} {DIM}@{RESET} "
      f"{BOLD}{team_color(home_abv)}{home_abv}{RESET}  "
      f"{GRAY}{game_time or status}{RESET}")
    p(f"  {GRAY}{'─' * 60}{RESET}")

    teams_box = box.get("teams") or {}
    _render_team_lineup("away", "away", teams_box.get("away", {}), away_pp, team_color(away_abv), _out)
    _render_team_lineup("home", "home", teams_box.get("home", {}), home_pp, team_color(home_abv), _out)

    # If neither lineup posted, add a hint
    away_bo = (teams_box.get("away") or {}).get("battingOrder") or []
    home_bo = (teams_box.get("home") or {}).get("battingOrder") or []
    if not away_bo and not home_bo:
        p(f"\n  {GRAY}Lineups are typically posted ~3 hours before first pitch.{RESET}")

    p()
    return buf.getvalue()


def _team_lineup_json(team_box: dict) -> dict:
    abv = team_box.get("team", {}).get("abbreviation", "??")
    bo = team_box.get("battingOrder") or []
    players = team_box.get("players") or {}
    out_players = []
    for i, pid in enumerate(bo, start=1):
        p = players.get(f"ID{pid}", {})
        season = (p.get("seasonStats") or {}).get("batting") or {}
        out_players.append({
            "order":    i,
            "id":       pid,
            "name":     (p.get("person") or {}).get("fullName"),
            "position": (p.get("position") or {}).get("abbreviation"),
            "avg":      season.get("avg"),
            "hr":       season.get("homeRuns"),
            "ops":      season.get("ops"),
        })
    return {"team": abv, "lineup": out_players}


def build_lineup_json(team_abv: str) -> dict:
    abv = team_abv.upper()
    if abv not in TEAMS:
        return {"error": f"Unknown team: {abv}"}
    games = _team_games_today(abv)
    if not games:
        return {"team": abv, "date": today_et().isoformat(), "game": None}

    game = next((g for g in games if g["status"]["abstractGameState"] != "Final"), games[0])
    box  = fetch_boxscore(game["gamePk"])
    teams_box = (box or {}).get("teams") or {}

    away_abv = abv_from_id(game["teams"]["away"]["team"]["id"])
    home_abv = abv_from_id(game["teams"]["home"]["team"]["id"])

    return {
        "team":      abv,
        "date":      today_et().isoformat(),
        "game_pk":   game["gamePk"],
        "game_time": game.get("gameDate"),
        "status":    game["status"].get("detailedState"),
        "matchup":   {"away": away_abv, "home": home_abv},
        "away":      _team_lineup_json(teams_box.get("away", {})),
        "home":      _team_lineup_json(teams_box.get("home", {})),
    }
