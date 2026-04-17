"""mlbsched web server — serves ANSI text to curl, HTML to browsers"""

from datetime import date, timedelta
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, HTMLResponse
import mlbsched as sched

app = FastAPI(docs_url=None, redoc_url=None)


def is_curl(request: Request) -> bool:
    ua = request.headers.get("user-agent", "").lower()
    return ua.startswith("curl")


def text(content: str):
    return PlainTextResponse(content)


def html_wrap(content: str) -> str:
    # Strip ANSI codes for browser display, wrap in a styled pre block
    import re
    clean = re.sub(r"\033\[[0-9;]*m", "", content)
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>mlbsched.run</title>
  <style>
    body {{ background: #0d1117; margin: 0; padding: 2rem; }}
    pre  {{ color: #e6edf3; font-family: 'Fira Mono', 'Courier New', monospace;
            font-size: 15px; line-height: 1.6; white-space: pre; }}
    a    {{ color: #58a6ff; }}
  </style>
</head>
<body><pre>{clean}</pre></body>
</html>"""


def respond(request: Request, content: str):
    if is_curl(request):
        return text(content)
    return HTMLResponse(html_wrap(content))


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/")
def root(request: Request):
    out = sched.render_schedule(date.today().strftime("%Y-%m-%d"))
    return respond(request, out)


@app.get("/tomorrow")
def tomorrow(request: Request):
    d = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")
    return respond(request, sched.render_schedule(d))


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
