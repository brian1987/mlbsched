"""wp — win probability for completed MLB games: the one-line sparkline /box
shows, and the /winprob chart of how the odds swung, still or drawn play by play."""

import asyncio
import io
import textwrap
from datetime import datetime
from zoneinfo import ZoneInfo
import requests

import watch
from mlbsched import (
    BOLD, RESET, GRAY, WHITE, RED,
    TEAMS,
    MLB_API,
    abv_from_id,
    is_doubleheader as sched_is_doubleheader,
    fetch_schedule,
    team_color,
)

BLOCKS = "▁▂▃▄▅▆▇█"  # 8 levels
CHART_WIDTH = 48


def fetch_wp(game_pk: int) -> list[dict]:
    """Return per-play WP entries from MLB's winProbability endpoint."""
    if not game_pk:
        return []
    try:
        resp = requests.get(f"{MLB_API}/game/{game_pk}/winProbability", timeout=10)
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _downsample(values: list[float], width: int) -> list[float]:
    n = len(values)
    if n == 0 or n <= width:
        return values
    out = []
    for i in range(width):
        start = (i * n) // width
        end = ((i + 1) * n) // width
        chunk = values[start:end] if end > start else [values[start]]
        out.append(sum(chunk) / len(chunk))
    return out


def _spark(values: list[float]) -> str:
    out = []
    for v in values:
        idx = min(7, max(0, int(v / 12.5)))
        out.append(BLOCKS[idx])
    return "".join(out)


def _ordinal(n: int) -> str:
    if 10 <= n % 100 <= 20:
        suf = "th"
    else:
        suf = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suf}"


def find_biggest_swing(plays: list[dict]) -> dict | None:
    if not plays:
        return None
    best = max(plays, key=lambda p: abs(p.get("homeTeamWinProbabilityAdded") or 0))
    wpa = best.get("homeTeamWinProbabilityAdded") or 0
    if abs(wpa) < 0.1:
        return None
    about = best.get("about") or {}
    result = best.get("result") or {}
    desc = (result.get("description") or "").strip()
    if desc.endswith("."):
        desc = desc[:-1]
    return {
        "delta_home": wpa,
        "abs_delta": abs(wpa),
        "half": about.get("halfInning", ""),
        "inning": about.get("inning"),
        "description": desc,
    }


def render_wp_for_game(game: dict) -> str:
    """Sparkline + headline-moment line for one Final game. Empty otherwise."""
    status = (game.get("status") or {}).get("abstractGameState", "")
    if status != "Final":
        return ""
    plays = fetch_wp(game.get("gamePk"))
    if not plays:
        return ""

    home_wp = [p.get("homeTeamWinProbability", 50.0) for p in plays]
    spark = _spark(_downsample(home_wp, CHART_WIDTH))

    home_abv = abv_from_id(game["teams"]["home"]["team"]["id"])
    away_abv = abv_from_id(game["teams"]["away"]["team"]["id"])
    final_home_wp = home_wp[-1]

    winner_abv = home_abv if final_home_wp >= 50 else away_abv
    winner_color = team_color(winner_abv)

    buf = io.StringIO()
    print(file=buf)
    print(
        f"  {GRAY}WP {RESET}{winner_color}{spark}{RESET}  "
        f"{BOLD}{winner_color}{winner_abv}{RESET} {GRAY}→{RESET} {WHITE}100%{RESET}",
        file=buf,
    )

    swing = find_biggest_swing(plays)
    if swing and swing["inning"] is not None:
        delta = swing["delta_home"]
        benefit_abv = home_abv if delta > 0 else away_abv
        pct = int(round(abs(delta)))
        half_short = "bot" if swing["half"] == "bottom" else "top"
        inn_str = f"{half_short} {_ordinal(swing['inning'])}"
        print(
            f"  {GRAY}↳ biggest swing: {swing['description']}, "
            f"{inn_str}  (+{pct}% {benefit_abv}){RESET}",
            file=buf,
        )

    return buf.getvalue()


# ── /winprob: the chart ────────────────────────────────────────────────────────
# A mountain chart around the 50% line: the home team's chance stacks up from it,
# the away team's hangs below it, one column per slice of the game. Half blocks
# only (no reverse video), so the browser view draws it too.
CHART_COLS = 60
CHART_ROWS = 15                     # odd: 7 rows each side of the 50% row
SWINGS = 3                          # plays marked on the chart and listed under it
PLAY_SECONDS = 20                   # the animation's length, before pauses
SWING_PAUSE = 2.0                   # hold on each marked play


def _half(play: dict) -> str:
    about = play.get("about") or {}
    return f"{'top' if about.get('isTopInning') else 'bot'} {_ordinal(about.get('inning') or 1)}"


def _wpa(play: dict) -> float:
    return play.get("homeTeamWinProbabilityAdded") or 0.0


def _score(play: dict) -> tuple[int, int]:
    result = play.get("result") or {}
    return result.get("awayScore") or 0, result.get("homeScore") or 0


def top_swings(plays: list[dict], k: int = SWINGS) -> list[int]:
    """Indexes of the k plays that moved the odds most, in game order."""
    return sorted(sorted(range(len(plays)), key=lambda i: -abs(_wpa(plays[i])))[:k])


def _play_at(c: int, n: int) -> int:
    """The play chart column c shows: the last of its share of the n plays."""
    return ((c + 1) * n - 1) // CHART_COLS


def _col_of(i: int, n: int) -> int:
    """The first chart column that shows play i (the inverse of _play_at)."""
    return min(CHART_COLS - 1, -(-(i * CHART_COLS + 1) // n) - 1)


def render_chart(plays: list[dict], home: str, away: str,
                 upto: int | None = None, marks: list[int] = ()) -> list[str]:
    """The chart's lines, drawn through play `upto` (all of them by default),
    with the plays in `marks` numbered 1, 2, 3 where they happened."""
    n = len(plays)
    side = CHART_ROWS // 2
    last = n if upto is None else upto
    hc, ac = team_color(home), team_color(away)
    grid = [[" "] * CHART_COLS for _ in range(CHART_ROWS)]
    ink = [[None] * CHART_COLS for _ in range(CHART_ROWS)]
    tip = {}                                            # column -> row just past its bar
    for c in range(CHART_COLS):
        j = _play_at(c, n)
        if j >= last:
            break
        v = plays[j].get("homeTeamWinProbability", 50.0)
        up = v >= 50
        units = round(abs(v - 50) / 50 * side * 2)      # half-cell steps away from 50%
        for k in range(units):
            r = side - 1 - k // 2 if up else side + 1 + k // 2
            grid[r][c] = "█" if k // 2 < units // 2 else ("▄" if up else "▀")
            ink[r][c] = hc if up else ac
        reach = (units + 1) // 2
        tip[c] = side - 1 - reach if up else side + 1 + reach
    for k, i in enumerate(marks, 1):
        if i < last:
            c = _col_of(i, n)
            r = min(max(tip.get(c, side - 1), 0), CHART_ROWS - 1)
            grid[r][c], ink[r][c] = str(k), "mark"

    lines = []
    for r in range(CHART_ROWS):
        label = {0: f"{BOLD}{hc}{home:>4}{RESET}", side: f"{GRAY} 50%{RESET}",
                 CHART_ROWS - 1: f"{BOLD}{ac}{away:>4}{RESET}"}.get(r, "    ")
        axis = "┼" if r == side else "┤" if r in (0, CHART_ROWS - 1) else "│"
        row = []
        for c in range(CHART_COLS):
            if ink[r][c] == "mark":
                row.append(f"{BOLD}{WHITE}{grid[r][c]}{RESET}")
            elif ink[r][c]:
                row.append(f"{ink[r][c]}{grid[r][c]}{RESET}")
            else:
                row.append(f"{GRAY}┈{RESET}" if r == side else " ")
        lines.append(f"  {label} {GRAY}{axis}{RESET}{''.join(row)}")

    ticks, nums = ["─"] * CHART_COLS, [" "] * (CHART_COLS + 2)
    seen = set()
    for i, p in enumerate(plays):
        about = p.get("about") or {}
        inning = about.get("inning")
        if about.get("isTopInning") and inning and inning not in seen:
            seen.add(inning)
            c = _col_of(i, n)
            ticks[c] = "┬"
            label = str(inning)
            if all(ch == " " for ch in nums[max(0, c - 1):c + len(label) + 1]):   # extras can crowd
                nums[c:c + len(label)] = label
    lines.append(f"       {GRAY}└{''.join(ticks)}{RESET}")
    lines.append(f"        {GRAY}{''.join(nums).rstrip()}{RESET}")
    return lines


def game_story(plays: list[dict], home: str, away: str) -> str:
    """One line: who won, the winner's lowest point, and how often the lead changed."""
    away_runs, home_runs = _score(plays[-1])
    if away_runs == home_runs:
        return f"{BOLD}{WHITE}Tied {home_runs}–{away_runs}.{RESET}"
    winner = home if home_runs > away_runs else away
    key = "homeTeamWinProbability" if winner == home else "awayTeamWinProbability"
    low, low_play = min(((p.get(key, 50.0), p) for p in plays), key=lambda t: t[0])
    leader = changes = 0
    for p in plays:
        a, h = _score(p)
        now = (h > a) - (h < a)
        if now and leader and now != leader:
            changes += 1
        leader = now or leader
    line = (f"{BOLD}{team_color(winner)}{winner}{RESET} {BOLD}{WHITE}won "
            f"{max(home_runs, away_runs)}–{min(home_runs, away_runs)}.{RESET} {GRAY}")
    if low >= 50:
        line += f"{winner} never trailed in the odds."
    else:
        line += f"{winner}'s low point: {round(low)}% ({_half(low_play)})."
    return line + f" {changes} lead change{'' if changes == 1 else 's'}.{RESET}"


def _swing_lines(plays: list[dict], home: str, away: str, marks: list[int]) -> list[str]:
    lines = [f"  {BOLD}{WHITE}Biggest swings{RESET}"]
    for k, i in enumerate(marks, 1):
        p = plays[i]
        who = home if _wpa(p) > 0 else away
        a, h = _score(p)
        lines.append(f"  {BOLD}{WHITE}{k}{RESET}  {_half(p):<8} {BOLD}{team_color(who)}+{round(abs(_wpa(p)))}% {who}{RESET}"
                     f"  {GRAY}{away} {a}, {home} {h}{RESET}")
        desc = ((p.get("result") or {}).get("description") or "").strip().rstrip(".")
        lines += [f"     {GRAY}{ln}{RESET}" for ln in textwrap.wrap(desc, 64, max_lines=2, placeholder=" …")]
    return lines


def _sides(game: dict) -> tuple[str, str]:
    return abv_from_id(game["teams"]["home"]["team"]["id"]), abv_from_id(game["teams"]["away"]["team"]["id"])


def render_game_chart(game: dict, plays: list[dict]) -> str:
    """One finished game's story line, chart and biggest swings."""
    home, away = _sides(game)
    marks = top_swings(plays)
    lines = ["", f"  {game_story(plays, home, away)}", ""]
    lines += render_chart(plays, home, away, marks=marks)
    lines += [""] + _swing_lines(plays, home, away, marks)
    return "\n".join(lines) + "\n"


def play_path(game: dict, team_abv: str) -> str:
    second = sched_is_doubleheader(game) and game.get("gameNumber") == 2
    return f"/winprob/{team_abv}/{game.get('officialDate')}?play=1{'&game=2' if second else ''}"


def render_winprob(team_abv: str, date_str: str, tz: ZoneInfo | None = None, out=None) -> str:
    """/winprob/<team>/<date>: the chart for each of the team's finished games that day."""
    import mlbsched as sched

    buf = io.StringIO()
    _out = out or buf

    def p(s=""):
        print(s, file=_out)

    abv = team_abv.upper()
    if abv not in TEAMS:
        p(f"{RED}Unknown team: {abv}{RESET}  —  try: curl mlbsched.run/teams")
        return buf.getvalue()

    team_id = TEAMS[abv][0]
    data = fetch_schedule(date_str, team_id)
    games = [g for blk in data.get("dates", []) for g in blk.get("games", [])]

    d = datetime.strptime(date_str, "%Y-%m-%d")
    label = d.strftime("%A, %B %-d, %Y")
    color = team_color(abv)

    p()
    p(f"  {BOLD}{color}{TEAMS[abv][1]}{RESET} — {BOLD}{WHITE}Win Probability{RESET}  {GRAY}{label}{RESET}")
    p(f"  {GRAY}{'─' * 52}{RESET}")

    if not games:
        p(f"  {GRAY}No games scheduled.{RESET}")
        p()
        return buf.getvalue()

    for game in games:
        sched._render_game_line(game, _out, tz=tz)
        status = (game.get("status") or {}).get("abstractGameState", "")
        if status != "Final" or sched._is_no_play(game):
            p(f"  {GRAY}The chart is drawn once the game is over.{RESET}")
            p()
            continue
        plays = fetch_wp(game.get("gamePk"))
        if not plays:
            p(f"  {GRAY}No win-probability data for this game.{RESET}")
            p()
            continue
        print(render_game_chart(game, plays), file=_out, end="")
        p()
        p(f"  {GRAY}Watch it fill in, play by play: curl -N \"mlbsched.run{play_path(game, abv)}\"{RESET}")
        p()

    return buf.getvalue()


def _ticker(play: dict, home: str, away: str) -> list[str]:
    """Three lines under the animated chart: where the game is, then the play."""
    a, h = _score(play)
    wp_home = play.get("homeTeamWinProbability", 50.0)
    fav, chance = (home, wp_home) if wp_home >= 50 else (away, 100 - wp_home)
    desc = ((play.get("result") or {}).get("description") or "").strip()
    wrapped = (textwrap.wrap(desc, 66, max_lines=2, placeholder=" …") + ["", ""])[:2]
    odds = f"{GRAY}even{RESET}" if round(chance) == 50 else f"{team_color(fav)}{fav} {round(chance)}%{RESET}"
    return [f"  {BOLD}{WHITE}{_half(play)}{RESET}  {away} {a}, {home} {h}  {GRAY}·{RESET}  {odds}"] + \
           [f"  {GRAY}{ln}{RESET}" for ln in wrapped]


async def play(game: dict, plays: list[dict], tz: ZoneInfo | None, is_disconnected):
    """Draw the chart play by play, holding on the biggest swings, then leave the
    finished chart on screen. Yields terminal output for a StreamingResponse."""
    import mlbsched as sched

    home, away = _sides(game)
    marks = top_swings(plays)
    step = min(0.5, max(0.05, PLAY_SECONDS / len(plays)))
    # The final score waits for the last frame, so the chart doesn't spoil the ending.
    day = datetime.strptime(game["officialDate"], "%Y-%m-%d").strftime("%A, %B %-d")
    matchup = (f"  {BOLD}{team_color(away)}{away}{RESET} {GRAY}@{RESET} {BOLD}{team_color(home)}{home}{RESET}"
               f"  {GRAY}{day}{RESET}")
    line = io.StringIO()
    sched._render_game_line(game, line, tz=tz)
    yield watch.CLEAR
    for upto in range(1, len(plays) + 1):
        frame = ["", matchup, ""] + render_chart(plays, home, away, upto=upto, marks=marks) + [""]
        frame += _ticker(plays[upto - 1], home, away)
        yield watch.paint("\n".join(frame))
        await asyncio.sleep(SWING_PAUSE if upto - 1 in marks else step)
        if await is_disconnected():
            return
    yield watch.paint("\n" + line.getvalue() + render_game_chart(game, plays))


def build_wp_json(team_abv: str, date_str: str) -> dict:
    abv = team_abv.upper()
    if abv not in TEAMS:
        return {"error": f"Unknown team: {abv}"}
    team_id = TEAMS[abv][0]
    data = fetch_schedule(date_str, team_id)
    games_out: list[dict] = []
    for blk in data.get("dates", []):
        for game in blk.get("games", []):
            status = (game.get("status") or {}).get("abstractGameState", "")
            game_pk = game.get("gamePk")
            entry = {
                "game_pk":       game_pk,
                "home":          abv_from_id(game["teams"]["home"]["team"]["id"]),
                "away":          abv_from_id(game["teams"]["away"]["team"]["id"]),
                "status":        status,
                "wp_home":       None,
                "biggest_swing": None,
            }
            if status == "Final":
                plays = fetch_wp(game_pk)
                if plays:
                    entry["wp_home"] = [round(p.get("homeTeamWinProbability", 50.0), 1) for p in plays]
                    entry["biggest_swing"] = find_biggest_swing(plays)
            games_out.append(entry)
    return {"team": abv, "date": date_str, "games": games_out}
