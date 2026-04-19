"""mlbsched web server — serves ANSI text to curl, HTML to browsers"""

from datetime import date, datetime, timedelta
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import mlbsched as sched

app = FastAPI(docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


def is_curl(request: Request) -> bool:
    ua = request.headers.get("user-agent", "").lower()
    return ua.startswith("curl")


def text(content: str):
    return PlainTextResponse(content)


def html_wrap(content: str, refresh_secs: int | None = None) -> str:
    import re
    clean = re.sub(r"\033\[[0-9;]*m", "", content)
    refresh_tag = f'<meta http-equiv="refresh" content="{refresh_secs}">' if refresh_secs else ""
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>mlbsched.run</title>
  {refresh_tag}
  <style>
    body {{ background: #0d1117; margin: 0; padding: 2rem; }}
    pre  {{ color: #e6edf3; font-family: 'Fira Mono', 'Courier New', monospace;
            font-size: 15px; line-height: 1.6; white-space: pre; }}
    a    {{ color: #58a6ff; }}
  </style>
</head>
<body><pre>{clean}</pre></body>
</html>"""


def respond(request: Request, content: str, refresh_secs: int | None = None):
    if is_curl(request):
        return text(content)
    return HTMLResponse(html_wrap(content, refresh_secs))


# ── JSON API ─────────────────────────────────────────────────────────────────

def build_game_json(game: dict) -> dict:
    away_id   = game["teams"]["away"]["team"]["id"]
    home_id   = game["teams"]["home"]["team"]["id"]
    away_abv  = sched.abv_from_id(away_id)
    home_abv  = sched.abv_from_id(home_id)
    abstract  = game["status"]["abstractGameState"]
    status    = game["status"]["detailedState"]
    linescore = game.get("linescore", {})

    game_time = None
    gt = game.get("gameDate", "")
    if gt:
        try:
            dt = datetime.strptime(gt, "%Y-%m-%dT%H:%M:%SZ")
            dt_et = dt.replace(hour=(dt.hour - 4) % 24)
            game_time = dt_et.strftime("%-I:%M %p ET")
        except Exception:
            pass

    return {
        "away": away_abv,
        "away_name": sched.TEAMS.get(away_abv, (None, away_abv, None))[1],
        "home": home_abv,
        "home_name": sched.TEAMS.get(home_abv, (None, home_abv, None))[1],
        "away_score": game["teams"]["away"].get("score"),
        "home_score": game["teams"]["home"].get("score"),
        "status": abstract,
        "detail": status,
        "inning": linescore.get("currentInning"),
        "inning_half": linescore.get("inningHalf"),
        "game_time": game_time,
    }


@app.get("/api/{team}")
def api_team(team: str):
    abv = team.upper()
    if abv not in sched.TEAMS:
        return JSONResponse({"error": f"Unknown team: {abv}"}, status_code=404)
    team_id = sched.TEAMS[abv][0]
    data = sched.fetch_schedule(date.today().strftime("%Y-%m-%d"), team_id)
    games = []
    for date_block in data.get("dates", []):
        for game in date_block.get("games", []):
            games.append(build_game_json(game))
    return JSONResponse({"team": abv, "date": date.today().isoformat(), "games": games})


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/")
def root(request: Request):
    out, has_live = sched.render_smart_today()
    return respond(request, out, refresh_secs=30 if has_live else None)


@app.get("/tomorrow")
def tomorrow(request: Request):
    d = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")
    return respond(request, sched.render_schedule(d))


@app.get("/live")
def live(request: Request):
    return respond(request, sched.render_live(), refresh_secs=30)


@app.get("/api/live")
def api_live():
    data = sched.fetch_schedule(date.today().strftime("%Y-%m-%d"))
    games = []
    for date_block in data.get("dates", []):
        for game in date_block.get("games", []):
            if game["status"]["abstractGameState"] == "Live":
                games.append(build_game_json(game))
    return JSONResponse({"date": date.today().isoformat(), "games": games})


@app.get("/standings")
def standings(request: Request):
    return respond(request, sched.render_standings())


@app.get("/teams")
def teams(request: Request):
    return respond(request, sched.render_team_list())


@app.get("/help")
def help_page(request: Request):
    return respond(request, sched.render_help())


@app.get("/{segment}")
def one_segment(request: Request, segment: str):
    # Could be a date (2026-04-20) or a team (NYY)
    try:
        d = sched.parse_date(segment)
        out = sched.render_schedule(d.strftime("%Y-%m-%d"))
    except ValueError:
        out = sched.render_schedule(date.today().strftime("%Y-%m-%d"), segment.upper())
    return respond(request, out)


@app.get("/tomorrow/{team}")
def tomorrow_team(request: Request, team: str):
    d = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")
    return respond(request, sched.render_schedule(d, team.upper()))


@app.get("/{team}/{date_str}")
def team_date(request: Request, team: str, date_str: str):
    try:
        d = sched.parse_date(date_str)
        out = sched.render_schedule(d.strftime("%Y-%m-%d"), team.upper())
    except ValueError:
        out = f"Invalid date: {date_str}\n"
    return respond(request, out)
