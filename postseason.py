"""postseason — bracket view of the MLB playoffs, round by round.

Built on MLB's `schedule/postseason/series` endpoint, which returns every
series of a season with its games. Series records are derived from Final games
(`isWinner` per side) rather than trusted from any single field, and a series
is "decided" once a side reaches the wins needed for its `gamesInSeries`.
Unplayed if-necessary games stay in the payload until the series ends, so
"games played" is counted from Finals, not from the list length."""

import io
import time
from datetime import datetime, timezone as _UTC
from zoneinfo import ZoneInfo

import requests

from mlbsched import (
    BOLD, DIM, RESET, GREEN, YELLOW, CYAN, WHITE, GRAY,
    MLB_API, ET, TEAMS, TEAM_ID_TO_ABV, team_color, fmt_game_time, today_et,
    series_tag, is_placeholder_team, team_label, game_time_label,
)

# Round order for display: gameType → (sort key, section title)
ROUNDS: dict[str, tuple[int, str]] = {
    "F": (0, "Wild Card Series"),
    "D": (1, "Division Series"),
    "L": (2, "League Championship Series"),
    "W": (3, "World Series"),
}

# Same TTL + serve-stale pattern as fetch_schedule: a burst collapses to one
# upstream call and an MLB hiccup serves the last good bracket.
_TTL_SECONDS = 60
_cache: dict[int, tuple[float, dict]] = {}   # season -> (fetched_at_monotonic, data)


def fetch_postseason(season: int) -> dict:
    now = time.monotonic()
    cached = _cache.get(season)
    if cached is not None and now - cached[0] < _TTL_SECONDS:
        return cached[1]
    try:
        resp = requests.get(
            f"{MLB_API}/schedule/postseason/series",
            params={"season": season, "sportId": 1, "hydrate": "team,linescore"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException:
        if cached is not None:
            return cached[1]
        raise
    _cache[season] = (now, data)
    return data


def _league(desc: str) -> str:
    """'AL' / 'NL' from a seriesDescription, '' for the World Series."""
    if desc.startswith("AL"):
        return "AL"
    if desc.startswith("NL"):
        return "NL"
    return ""


def _bracket_slot(description: str) -> str:
    """The 'A' / 'B' in "ALDS 'B' Game 1" — orders series within a round."""
    if "'A'" in description:
        return "A"
    if "'B'" in description:
        return "B"
    return ""


def summarize_series(entry: dict) -> dict | None:
    """Normalize one series payload into a display-ready dict."""
    games = entry.get("games") or []
    if not games:
        return None
    first = games[0]
    game_type = first.get("gameType") or (entry.get("series") or {}).get("gameType", "")
    if game_type not in ROUNDS:
        return None

    desc = first.get("seriesDescription", "")
    best_of = first.get("gamesInSeries") or len(games)
    needed = best_of // 2 + 1

    # Identify the two teams from game 1 (home/away flip across the series).
    t1 = first["teams"]["home"]["team"]
    t2 = first["teams"]["away"]["team"]
    ids = (t1.get("id"), t2.get("id"))

    wins = {ids[0]: 0, ids[1]: 0}
    played: list[dict] = []
    live: dict | None = None
    next_game: dict | None = None
    for g in games:
        state = g["status"]["abstractGameState"]
        if state == "Final":
            played.append(g)
            for side in ("home", "away"):
                team = g["teams"][side]
                if team.get("isWinner"):
                    wins[team["team"]["id"]] = wins.get(team["team"]["id"], 0) + 1
        elif state == "Live" and live is None:
            live = g
        elif next_game is None and state not in ("Final", "Live"):
            next_game = g

    w1, w2 = wins.get(ids[0], 0), wins.get(ids[1], 0)
    winner = None
    if w1 >= needed:
        winner = t1
    elif w2 >= needed:
        winner = t2

    # Higher seed listed first: game 1's home team hosts by rule.
    return {
        "game_type":   game_type,
        "round":       ROUNDS[game_type][1],
        "league":      _league(desc),
        "slot":        _bracket_slot(first.get("description", "")),
        "description": desc,
        "best_of":     best_of,
        "needed":      needed,
        "top":         t1,
        "bottom":      t2,
        "top_wins":    w1,
        "bottom_wins": w2,
        "winner":      winner,
        "played":      played,
        "live":        live,
        "next":        next_game,
        "games":       games,
    }


def get_bracket(season: int) -> list[dict]:
    data = fetch_postseason(season)
    out = []
    for entry in data.get("series", []):
        s = summarize_series(entry)
        if s:
            out.append(s)
    out.sort(key=lambda s: (ROUNDS[s["game_type"]][0], s["league"], s["slot"]))
    return out


# ── Rendering ─────────────────────────────────────────────────────────────────

def _team_cell(team: dict, width: int = 3) -> str:
    """Colored abbreviation, or a gray TBD for a placeholder slot."""
    if is_placeholder_team(team):
        return f"{GRAY}{'TBD':<{width}}{RESET}"
    abv = team_label(team)
    return f"{BOLD}{team_color(abv)}{abv:<{width}}{RESET}"


def _short_date(iso: str, tz: ZoneInfo | None) -> str:
    try:
        dt = datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_UTC.utc)
        return dt.astimezone(tz or ET).strftime("%a %-m/%-d")
    except (ValueError, TypeError):
        return ""


def _status_text(s: dict, tz: ZoneInfo | None) -> str:
    """Right-hand status: champion, live score, next game, or start date."""
    if s["winner"] is not None:
        w = s["winner"]
        label = team_label(w)
        return (f"{BOLD}{team_color(label)}{label}{RESET} {GRAY}win "
                f"{max(s['top_wins'], s['bottom_wins'])}-{min(s['top_wins'], s['bottom_wins'])}{RESET}")
    if s["live"] is not None:
        g = s["live"]
        ls = g.get("linescore", {})
        arrow = "▲" if (ls.get("inningHalf") or "").lower() == "top" else "▼"
        away, home = g["teams"]["away"], g["teams"]["home"]
        return (f"{GREEN}{BOLD}● G{g.get('seriesGameNumber', '?')} {arrow}{ls.get('currentInning', '')}{RESET}  "
                f"{_team_cell(away['team'])} {GREEN}{away.get('score', 0):>2}{RESET}  "
                f"{_team_cell(home['team'])} {GREEN}{home.get('score', 0):>2}{RESET}")
    if s["next"] is not None:
        g = s["next"]
        when = _short_date(g.get("gameDate", ""), tz)
        t = game_time_label(g, tz)
        num = g.get("seriesGameNumber")
        prefix = f"G{num} " if num else ""
        return f"{CYAN}{prefix}{when} {t}".rstrip() + RESET
    return f"{GRAY}—{RESET}"


def _series_line(s: dict, tz: ZoneInfo | None) -> str:
    tag = s["league"] + {"F": "WC", "D": "DS", "L": "CS", "W": ""}[s["game_type"]]
    if s["game_type"] == "W":
        tag = "WS"
    slot = f" {s['slot']}" if s["slot"] else "  "
    top, bottom = s["top"], s["bottom"]
    decided = s["winner"] is not None
    started = bool(s["played"]) or s["live"] is not None

    def score(n: int, is_winner: bool) -> str:
        if not started:
            return "  "          # blank cell keeps the columns aligned
        color = f"{BOLD}{WHITE}" if is_winner else GRAY
        return f"{color}{n:>2}{RESET}"

    sep = f"{DIM}{('-' if started else 'vs'):<2}{RESET}"   # pad the text, not the escape codes
    top_win = decided and s["winner"] is top
    bot_win = decided and s["winner"] is bottom
    return (
        f"    {GRAY}{tag:<4}{slot}{RESET}  "
        f"{_team_cell(top)} {score(s['top_wins'], top_win)} {sep} "
        f"{_team_cell(bottom)} {score(s['bottom_wins'], bot_win)}   "
        f"{_status_text(s, tz)}"
    )


def _placeholder_note(s: dict) -> str | None:
    """'AL Wild Card #3 @ AL West #1' when a slot isn't filled yet."""
    if not (is_placeholder_team(s["top"]) or is_placeholder_team(s["bottom"])):
        return None
    return f"{s['bottom'].get('name', 'TBD')} @ {s['top'].get('name', 'TBD')}"


def render_postseason(season: int | None = None, out=None, tz: ZoneInfo | None = None) -> str:
    buf = io.StringIO()
    _out = out or buf

    def p(s=""):
        print(s, file=_out)

    season = season or today_et().year

    p()
    p(f"  {BOLD}{CYAN}MLB Postseason{RESET} — {BOLD}{WHITE}{season}{RESET}")
    p(f"  {GRAY}{'─' * 60}{RESET}")

    try:
        bracket = get_bracket(season)
    except requests.RequestException:
        p(f"  {YELLOW}Could not reach MLB API.{RESET}")
        p()
        return buf.getvalue()

    if not bracket:
        p(f"  {GRAY}No postseason schedule published for {season} yet.{RESET}")
        p()
        return buf.getvalue()

    current_round = None
    for s in bracket:
        if s["game_type"] != current_round:
            current_round = s["game_type"]
            p()
            p(f"  {BOLD}{YELLOW}{s['round']}{RESET}  {GRAY}(best of {s['best_of']}){RESET}")
        p(_series_line(s, tz))
        note = _placeholder_note(s)
        if note:
            p(f"             {DIM}{note}{RESET}")

    champ = next((s["winner"] for s in bracket if s["game_type"] == "W" and s["winner"]), None)
    p()
    if champ is not None:
        label = team_label(champ)
        name = TEAMS.get(label, (None, champ.get("name", label), None))[1]
        p(f"  {BOLD}{GREEN}🏆 {season} World Series champions: {team_color(label)}{name}{RESET}")
        p()
    p(f"  {GRAY}Series record shown left; higher seed listed first. "
      f"Boxscores: curl mlbsched.run/box/<TEAM>/<DATE>{RESET}")
    p()
    return buf.getvalue()


# ── JSON ──────────────────────────────────────────────────────────────────────

def _team_json(team: dict) -> dict:
    placeholder = is_placeholder_team(team)
    return {
        "team":        None if placeholder else team_label(team),
        "name":        team.get("name"),
        "placeholder": placeholder,
    }


def _game_json(g: dict) -> dict:
    away, home = g["teams"]["away"], g["teams"]["home"]
    status = g["status"]["abstractGameState"]
    winner = None
    if status == "Final":
        if away.get("isWinner"):
            winner = _team_json(away["team"])["team"]
        elif home.get("isWinner"):
            winner = _team_json(home["team"])["team"]
    return {
        "game_pk":       g.get("gamePk"),
        "game_number":   g.get("seriesGameNumber"),
        "date":          g.get("officialDate"),
        "game_date":     g.get("gameDate"),
        "start_time_tbd": bool(g["status"].get("startTimeTBD")),
        "if_necessary":  g.get("ifNecessary") == "Y",
        "away":          _team_json(away["team"]),
        "home":          _team_json(home["team"]),
        "away_score":    away.get("score"),
        "home_score":    home.get("score"),
        "status":        status,
        "detail":        g["status"].get("detailedState"),
        "winner":        winner,
    }


def build_postseason_json(season: int | None = None) -> dict:
    season = season or today_et().year
    try:
        bracket = get_bracket(season)
    except requests.RequestException as e:
        return {"error": f"upstream: {e}"}
    series_out = []
    for s in bracket:
        winner = _team_json(s["winner"])["team"] if s["winner"] else None
        series_out.append({
            "round":       s["round"],
            "game_type":   s["game_type"],
            "league":      s["league"] or None,
            "slot":        s["slot"] or None,
            "description": s["description"],
            "best_of":     s["best_of"],
            "top":         _team_json(s["top"]),
            "bottom":      _team_json(s["bottom"]),
            "top_wins":    s["top_wins"],
            "bottom_wins": s["bottom_wins"],
            "winner":      winner,
            "status":      "final" if winner else ("live" if s["live"] else ("in_progress" if s["played"] else "scheduled")),
            "games":       [_game_json(g) for g in s["games"]],
        })
    champ = next((s["winner"] for s in series_out if s["game_type"] == "W" and s["winner"]), None)
    return {"season": season, "champion": champ, "series": series_out}
