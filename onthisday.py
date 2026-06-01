"""onthisday — games played on this calendar date in MLB history"""

import io
from datetime import date

from mlbsched import (
    BOLD, RESET, GRAY, GREEN, WHITE, DIM,
    abv_from_team,
    fetch_schedule,
    fmt_team,
    fmt_score,
    today_et,
)


YEARS_BACK = [10, 25, 50]


def _games_for(year: int, month: int, day: int) -> list[dict]:
    try:
        d = date(year, month, day)
    except ValueError:
        return []
    try:
        data = fetch_schedule(d.isoformat())
    except Exception:
        return []
    games: list[dict] = []
    for block in data.get("dates", []):
        for g in block.get("games", []):
            games.append(g)
    return games


def _final_score_line(game: dict) -> str | None:
    status = (game.get("status") or {}).get("abstractGameState", "")
    if status != "Final":
        return None
    away = game["teams"]["away"]
    home = game["teams"]["home"]
    away_score = away.get("score")
    home_score = home.get("score")
    if away_score is None or home_score is None:
        return None
    away_abv = abv_from_team(away["team"])
    home_abv = abv_from_team(home["team"])
    a_str = fmt_team(away_abv)
    h_str = fmt_team(home_abv)
    if away_score > home_score:
        a_sc = f"{BOLD}{WHITE}{fmt_score(away_score)}{RESET}"
        h_sc = f"{BOLD}{fmt_score(home_score)}{RESET}"
    else:
        a_sc = f"{BOLD}{fmt_score(away_score)}{RESET}"
        h_sc = f"{BOLD}{WHITE}{fmt_score(home_score)}{RESET}"
    return f"    {a_str} {a_sc}  {DIM}@{RESET}  {h_str} {h_sc}"


def render_onthisday(out=None) -> str:
    buf = io.StringIO()
    _out = out or buf

    today = today_et()
    label = today.strftime("%B %-d")

    print(file=_out)
    print(f"  {BOLD}On this date in MLB history — {label}{RESET}", file=_out)
    print(file=_out)

    any_games = False
    for years_ago in YEARS_BACK:
        year = today.year - years_ago
        games = _games_for(year, today.month, today.day)
        lines = [ln for ln in (_final_score_line(g) for g in games) if ln]

        print(f"  {GREEN}{BOLD}{years_ago} years ago ({year}){RESET}", file=_out)
        if not lines:
            print(f"    {GRAY}No completed games.{RESET}", file=_out)
        else:
            any_games = True
            for ln in lines:
                print(ln, file=_out)
        print(file=_out)

    if not any_games:
        print(f"  {GRAY}No MLB games found on this date in the years above.{RESET}", file=_out)
        print(file=_out)

    return buf.getvalue()


def build_onthisday_json() -> dict:
    today = today_et()
    out: dict = {
        "kind":  "this_day_in_history",
        "month": today.month,
        "day":   today.day,
        "years": [],
    }
    for years_ago in YEARS_BACK:
        year = today.year - years_ago
        games = _games_for(year, today.month, today.day)
        block: dict = {"year": year, "years_ago": years_ago, "games": []}
        for g in games:
            status = (g.get("status") or {}).get("abstractGameState", "")
            if status != "Final":
                continue
            away = g["teams"]["away"]
            home = g["teams"]["home"]
            away_score = away.get("score")
            home_score = home.get("score")
            if away_score is None or home_score is None:
                continue
            block["games"].append({
                "away":       abv_from_team(away["team"]),
                "home":       abv_from_team(home["team"]),
                "away_score": away_score,
                "home_score": home_score,
            })
        out["years"].append(block)
    return out
