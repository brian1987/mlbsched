"""mascot — random MLB mascot ASCII art for /random"""

import io
import random as _random

from mlbsched import BOLD, RESET, BLUE, WHITE, GRAY, RED

ORANGE = "\033[38;5;208m"


# Each art line is a list of (text, color) segments so we can mix colors
# within a line (e.g. orange "NY" on a blue cap).
MASCOTS = [
    {
        "name":    "Mr. Met",
        "team":    "NYM",
        "tagline": "Let's Go Mets!",
        "art": [
            [("                _________",                  BLUE)],
            [("               |  ",         BLUE), ("N Y",  ORANGE), ("    |",   BLUE)],
            [("           ____|_________|____",             BLUE)],
            [("          /                   \\",           WHITE)],
            [("         /                     \\",          WHITE)],
            [("        |    O            O     |",          WHITE)],
            [("        |                       |",          WHITE)],
            [("        |      \\_________/      |",         RED)],
            [("         \\                     /",          WHITE)],
            [("          \\___________________/",           WHITE)],
            [("               |         |",                 GRAY)],
            [("           ____|_________|____",             BLUE)],
            [("          |                   |",            BLUE)],
            [("          |     ",            BLUE), ("M  E  T  S", ORANGE), ("    |", BLUE)],
            [("          |___________________|",            BLUE)],
            [("              /             \\",             GRAY)],
            [("             /               \\",            GRAY)],
            [("            /_________________\\",           GRAY)],
        ],
    },
]


def _render_line(segments) -> str:
    return "".join(f"{color}{text}{RESET}" for text, color in segments)


def _plain_line(segments) -> str:
    return "".join(text for text, _ in segments)


def render_random(out=None) -> str:
    buf = io.StringIO()
    _out = out or buf

    mascot = _random.choice(MASCOTS)

    print(file=_out)
    for segments in mascot["art"]:
        print(_render_line(segments), file=_out)
    print(file=_out)
    print(f"          {BOLD}{ORANGE}{mascot['tagline']}{RESET}", file=_out)
    print(f"          {GRAY}— {mascot['name']}, {mascot['team']}{RESET}", file=_out)
    print(file=_out)

    return buf.getvalue()


def build_random_json() -> dict:
    mascot = _random.choice(MASCOTS)
    return {
        "kind":    "mascot",
        "name":    mascot["name"],
        "team":    mascot["team"],
        "tagline": mascot["tagline"],
        "art":     "\n".join(_plain_line(seg) for seg in mascot["art"]),
    }
