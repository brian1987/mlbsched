"""pitchers — today's probable starting-pitcher matchups across the slate.

Reuses the already-hydrated/enriched schedule from mlbsched.fetch_schedule:
each probablePitcher carries season W-L + ERA + WHIP (_record) and handedness
(_hand) spliced in by _enrich_probable_pitchers."""

import io
from zoneinfo import ZoneInfo

import requests

import mlbsched as sched
from mlbsched import (
    BOLD, DIM, RESET, YELLOW, CYAN, WHITE, GRAY,
    abv_from_id, fmt_team, fmt_game_time, today_et,
)

_HAND = {"R": "RHP", "L": "LHP", "S": "SP"}


def _hand_label(pp: dict) -> str:
    return _HAND.get(pp.get("_hand", ""), "")


def _stat_bits(pp: dict) -> str:
    """Trailing ' W-L  E.RA ERA  W.HIP WHIP' fragment, or '' when no stats."""
    rec = pp.get("_record") or {}
    w, l, era, whip = rec.get("wins"), rec.get("losses"), rec.get("era"), rec.get("whip")
    bits = []
    if w is not None and l is not None:
        bits.append(f"{w}-{l}")
    if era is not None:
        bits.append(f"{era} ERA")
    if whip is not None:
        bits.append(f"{whip} WHIP")
    return "   ".join(bits)


def _game_state(game: dict, tz: ZoneInfo | None) -> str:
    abstract = (game.get("status") or {}).get("abstractGameState", "")
    if abstract == "Preview":
        return fmt_game_time(game.get("gameDate", ""), tz) or "TBD"
    if abstract == "Final":
        return "Final"
    return abstract or "TBD"


def render_pitchers(out=None, tz: ZoneInfo | None = None) -> str:
    buf = io.StringIO()
    _out = out or buf

    def p(s=""):
        print(s, file=_out)

    today = today_et()
    label = today.strftime("%A, %B %-d, %Y")

    p()
    p(f"  {BOLD}{CYAN}Probable Pitchers{RESET} — {BOLD}{WHITE}{label}{RESET}")
    p(f"  {GRAY}{'─' * 60}{RESET}")

    try:
        data = sched.fetch_schedule(today.strftime("%Y-%m-%d"))
    except requests.RequestException:
        p(f"  {YELLOW}Could not reach MLB API.{RESET}")
        p()
        return buf.getvalue()

    games = [g for block in data.get("dates", []) for g in block.get("games", [])]
    if not games:
        p(f"  {GRAY}No games scheduled.{RESET}")
        p()
        return buf.getvalue()

    for game in games:
        away_abv = abv_from_id(game["teams"]["away"]["team"]["id"])
        home_abv = abv_from_id(game["teams"]["home"]["team"]["id"])
        state = _game_state(game, tz)

        p()
        p(f"  {fmt_team(away_abv)} {DIM}@{RESET} {fmt_team(home_abv)}   {CYAN}{state}{RESET}")

        for side, abv in (("away", away_abv), ("home", home_abv)):
            pp = game["teams"][side].get("probablePitcher")
            if not pp:
                p(f"    {fmt_team(abv)}  {GRAY}TBD{RESET}")
                continue
            name = pp.get("fullName", "TBD")
            hand = _hand_label(pp)
            stats = _stat_bits(pp)
            line = f"    {fmt_team(abv)}  {name:<20}"
            if hand:
                line += f" {GRAY}{hand}{RESET}"
            if stats:
                line += f"   {GRAY}{stats}{RESET}"
            p(line)

    p()
    p(f"  {GRAY}Season W-L, ERA, WHIP. Source: MLB Stats API.{RESET}")
    p()
    return buf.getvalue()


def _pitcher_json(pp: dict | None) -> dict | None:
    if not pp:
        return None
    rec = pp.get("_record") or {}
    return {
        "id":     pp.get("id"),
        "name":   pp.get("fullName"),
        "hand":   pp.get("_hand"),
        "wins":   rec.get("wins"),
        "losses": rec.get("losses"),
        "era":    rec.get("era"),
        "whip":   rec.get("whip"),
    }


def build_pitchers_json(tz: ZoneInfo | None = None) -> dict:
    today = today_et()
    try:
        data = sched.fetch_schedule(today.strftime("%Y-%m-%d"))
    except requests.RequestException as e:
        return {"error": f"upstream: {e}"}

    games_out = []
    for block in data.get("dates", []):
        for game in block.get("games", []):
            away_abv = abv_from_id(game["teams"]["away"]["team"]["id"])
            home_abv = abv_from_id(game["teams"]["home"]["team"]["id"])
            games_out.append({
                "away":          away_abv,
                "home":          home_abv,
                "game_time":     fmt_game_time(game.get("gameDate", ""), tz) or None,
                "status":        (game.get("status") or {}).get("abstractGameState"),
                "away_pitcher":  _pitcher_json(game["teams"]["away"].get("probablePitcher")),
                "home_pitcher":  _pitcher_json(game["teams"]["home"].get("probablePitcher")),
            })

    return {"date": today.isoformat(), "games": games_out}
