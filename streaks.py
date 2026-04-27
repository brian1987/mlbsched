"""streaks — teams currently on hot or cold runs"""

import io
import random

from mlbsched import (
    BOLD, DIM, RESET, RED, GREEN, CYAN, GRAY,
    TEAMS, abv_from_id, team_color, fetch_standings,
)

DEFAULT_MIN = 4

WIN_QUIPS: dict[tuple[int, float], list[str]] = {
    (4, 5): [
        "on a heater",
        "rolling",
        "looking like a real ballclub",
        "vibes immaculate",
    ],
    (6, 8): [
        "possessed",
        "starting to scare people",
        "playoff-tuned",
        "the bullpen is sleeping well",
    ],
    (9, float("inf")): [
        "call the league office",
        "running out of opponents",
        "dynasty energy",
        "someone check the bats for cork",
    ],
}

LOSS_QUIPS: dict[tuple[int, float], list[str]] = {
    (4, 5): [
        "rough patch",
        "trade-deadline mood",
        "the vibes are off",
        "every loss is a learning opportunity, allegedly",
    ],
    (6, 8): [
        "fans considering new hobbies",
        "tarp's getting more wins",
        "the manager is updating his resume",
        "the dugout has gone quiet",
    ],
    (9, float("inf")): [
        "burn it down",
        "rebuild incoming",
        "Triple-A could probably take 'em",
        "calling owners about the next draft pick",
    ],
}

METS_LOSS_BONUS: list[str] = [
    "LOLMets",
    "Cohen's wallet weeps",
    "same as it ever was",
    "the curse is load-bearing",
    "Mr. Met has seen things",
]


def _pick_quip(streak_type: str, n: int, abv: str) -> str:
    table = WIN_QUIPS if streak_type == "wins" else LOSS_QUIPS
    for (lo, hi), pool in table.items():
        if lo <= n <= hi:
            quips = list(pool)
            if streak_type == "losses" and abv == "NYM":
                quips = quips + METS_LOSS_BONUS
            return random.choice(quips)
    return ""


def get_streaks(min_streak: int = DEFAULT_MIN) -> tuple[list[dict], list[dict]]:
    """Return (winning, losing) lists of streak dicts, each sorted by streak length desc."""
    data = fetch_standings()
    winning: list[dict] = []
    losing:  list[dict] = []
    for record in data.get("records", []):
        for t in record.get("teamRecords", []):
            streak = t.get("streak") or {}
            n = streak.get("streakNumber") or 0
            stype = streak.get("streakType")
            if n < min_streak or stype not in ("wins", "losses"):
                continue
            team_id = t["team"]["id"]
            abv = abv_from_id(team_id)
            entry = {
                "abv":    abv,
                "name":   TEAMS.get(abv, (None, t["team"]["name"], None))[1],
                "type":   stype,
                "n":      n,
                "code":   streak.get("streakCode", ""),
                "wins":   t.get("wins"),
                "losses": t.get("losses"),
                "quip":   _pick_quip(stype, n, abv),
            }
            (winning if stype == "wins" else losing).append(entry)
    winning.sort(key=lambda x: -x["n"])
    losing.sort(key=lambda x: -x["n"])
    return winning, losing


def _render_row(s: dict, color: str, out) -> None:
    abv = s["abv"]
    record = f"{s['wins']}-{s['losses']}"
    print(
        f"  {BOLD}{team_color(abv)}{abv:<3}{RESET}  "
        f"{BOLD}{color}{s['code']:>3}{RESET}  "
        f"{GRAY}{record:>7}{RESET}  "
        f"{s['name']:<24}  "
        f"{DIM}{s['quip']}{RESET}",
        file=out,
    )


def render_streaks(min_streak: int = DEFAULT_MIN, out=None) -> str:
    buf = io.StringIO()
    _out = out or buf

    def p(s=""):
        print(s, file=_out)

    winning, losing = get_streaks(min_streak)

    p()
    p(f"  {BOLD}{CYAN}Streaks{RESET}  {GRAY}({min_streak}+ games){RESET}")
    p(f"  {GRAY}{'─' * 52}{RESET}")

    if not winning and not losing:
        p(f"  {GRAY}No streaks of {min_streak}+ games right now. Parity!{RESET}")
        p()
        return buf.getvalue()

    if winning:
        p(f"  {BOLD}{GREEN}Hot{RESET}")
        for s in winning:
            _render_row(s, GREEN, _out)
        if losing:
            p()

    if losing:
        p(f"  {BOLD}{RED}Cold{RESET}")
        for s in losing:
            _render_row(s, RED, _out)

    p()
    return buf.getvalue()


def build_streak_json(s: dict) -> dict:
    return {
        "team":   s["abv"],
        "name":   s["name"],
        "type":   s["type"],
        "length": s["n"],
        "code":   s["code"],
        "wins":   s["wins"],
        "losses": s["losses"],
        "quip":   s["quip"],
    }
