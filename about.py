"""about — what mlbsched.run is, how it's built, and who made it"""

import io

from mlbsched import BOLD, RESET, GRAY, DIM, CYAN, WHITE, GREEN

ORANGE = "\033[38;5;208m"
BLUE   = "\033[94m"

GITHUB = "github.com/brian1987/mlbsched"
SITE   = "brianpisano.com"


def render_about(out=None) -> str:
    buf = io.StringIO()
    _out = out or buf

    print(f"""
  {BOLD}{CYAN}mlbsched.run{RESET} — the MLB, in your terminal

  Live scores, schedules, standings, boxscores, odds, weather, and
  stats — served as plain text you can {BOLD}curl{RESET}. No app, no signup,
  no JavaScript. Works the same in your shell, a script, or a cron job.

  {BOLD}Try it:{RESET}
    curl mlbsched.run                      Today's full schedule
    curl mlbsched.run/{ORANGE}NYM{RESET}                  One team's game today
    curl mlbsched.run/standings            Division standings
    curl mlbsched.run/help                 Every command

  {BOLD}How it's built:{RESET}
    {GRAY}Data{RESET}     MLB Stats API (statsapi.mlb.com) — free, no key
    {GRAY}Server{RESET}   FastAPI + uvicorn, single box on fly.io
    {GRAY}Trick{RESET}    content-negotiated: curl gets ANSI text, a browser
             gets the same view wrapped in HTML

  {BOLD}Open source — star it, fork it, send a PR:{RESET}
    {GREEN}https://{GITHUB}{RESET}

  {BOLD}Made by Brian Pisano.{RESET}
    {BLUE}https://{SITE}{RESET}
    {DIM}Lifelong Mets fan and general man about New York City.
    Interests include economics, public policy, NYC history, and
    baseball. Open to speaking engagements, consulting, and a good
    conversation — reach out any time.{RESET}

  {GRAY}Not affiliated with MLB. Data belongs to MLBAM.{RESET}
""", file=_out)
    return buf.getvalue()


def build_about_json() -> dict:
    return {
        "kind":        "about",
        "name":        "mlbsched.run",
        "tagline":     "The MLB, in your terminal.",
        "description": (
            "Live MLB scores, schedules, standings, boxscores, odds, "
            "weather, and stats served as plain text you can curl."
        ),
        "data_source": "MLB Stats API (statsapi.mlb.com)",
        "stack":       ["FastAPI", "uvicorn", "fly.io"],
        "source":      f"https://{GITHUB}",
        "author":      "Brian Pisano",
        "author_site": f"https://{SITE}",
        "help":        "https://mlbsched.run/help",
    }
