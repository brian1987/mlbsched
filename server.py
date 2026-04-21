"""mlbsched web server — serves ANSI text to curl, HTML to browsers"""

from datetime import date, datetime, timedelta, timezone as _UTC
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import requests as _requests
import mlbsched as sched
from mlbsched import today_et

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


# ── IP geolocation ────────────────────────────────────────────────────────────

def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host


def geolocate_ip(ip: str) -> dict | None:
    """Returns {lat, lon, city, region, country, timezone} or None on failure/private IP."""
    try:
        resp = _requests.get(f"http://ip-api.com/json/{ip}", timeout=3)
        data = resp.json()
        if data.get("status") == "success":
            return {
                "lat": data["lat"],
                "lon": data["lon"],
                "city": data.get("city", ""),
                "region": data.get("regionName", ""),
                "country": data.get("country", ""),
                "timezone": data.get("timezone", ""),
            }
    except Exception:
        pass
    return None


def get_user_tz(geo: dict | None) -> ZoneInfo | None:
    """Convert ip-api timezone string to ZoneInfo, falling back to None (→ ET)."""
    if not geo:
        return None
    tz_name = geo.get("timezone", "")
    if not tz_name:
        return None
    try:
        return ZoneInfo(tz_name)
    except (ZoneInfoNotFoundError, KeyError):
        return None


# ── JSON API ─────────────────────────────────────────────────────────────────

def build_game_json(game: dict, user_lat: float | None = None, user_lon: float | None = None, tz: ZoneInfo | None = None) -> dict:
    away_id   = game["teams"]["away"]["team"]["id"]
    home_id   = game["teams"]["home"]["team"]["id"]
    away_abv  = sched.abv_from_id(away_id)
    home_abv  = sched.abv_from_id(home_id)
    abstract  = game["status"]["abstractGameState"]
    status    = game["status"]["detailedState"]
    linescore = game.get("linescore", {})

    game_time = sched.fmt_game_time(game.get("gameDate", ""), tz) or None

    loc = sched.game_location(game)
    stadium_name = loc[0] if loc else None
    stadium_lat  = loc[1] if loc else None
    stadium_lon  = loc[2] if loc else None

    distance_miles = None
    if user_lat is not None and user_lon is not None and stadium_lat is not None:
        distance_miles = round(sched.haversine(user_lat, user_lon, stadium_lat, stadium_lon), 1)

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
        "stadium": stadium_name,
        "stadium_lat": stadium_lat,
        "stadium_lon": stadium_lon,
        "distance_miles": distance_miles,
    }


# specific /api/* routes must be registered before /api/{team}
@app.get("/api/live")
def api_live(request: Request):
    data = sched.fetch_schedule(today_et().strftime("%Y-%m-%d"))
    geo = geolocate_ip(get_client_ip(request))
    user_lat = geo["lat"] if geo else None
    user_lon = geo["lon"] if geo else None
    tz = get_user_tz(geo)
    games = []
    for date_block in data.get("dates", []):
        for game in date_block.get("games", []):
            if game["status"]["abstractGameState"] == "Live":
                games.append(build_game_json(game, user_lat, user_lon, tz))
    return JSONResponse({"date": today_et().isoformat(), "games": games})


@app.get("/api/distance")
def api_distance(request: Request, lat: float | None = None, lon: float | None = None):
    geo = None
    if lat is None or lon is None:
        ip = get_client_ip(request)
        geo = geolocate_ip(ip)
        if not geo:
            return JSONResponse({"error": "Could not geolocate IP. Pass ?lat=XX&lon=YY explicitly."}, status_code=400)
        lat, lon = geo["lat"], geo["lon"]
        city = f"{geo['city']}, {geo['region']}" if geo.get("region") else geo.get("city", "")
    else:
        city = None

    data = sched.fetch_schedule(today_et().strftime("%Y-%m-%d"))
    tz = get_user_tz(geo)
    games = []
    for date_block in data.get("dates", []):
        for game in date_block.get("games", []):
            g = build_game_json(game, lat, lon, tz)
            games.append(g)

    games.sort(key=lambda g: g["distance_miles"] if g["distance_miles"] is not None else float("inf"))
    return JSONResponse({"date": today_et().isoformat(), "user_lat": lat, "user_lon": lon, "user_city": city, "games": games})


@app.get("/api/{team}")
def api_team(request: Request, team: str):
    abv = team.upper()
    if abv not in sched.TEAMS:
        return JSONResponse({"error": f"Unknown team: {abv}"}, status_code=404)
    team_id = sched.TEAMS[abv][0]
    data = sched.fetch_schedule(today_et().strftime("%Y-%m-%d"), team_id)
    geo = geolocate_ip(get_client_ip(request))
    user_lat = geo["lat"] if geo else None
    user_lon = geo["lon"] if geo else None
    tz = get_user_tz(geo)
    games = []
    for date_block in data.get("dates", []):
        for game in date_block.get("games", []):
            games.append(build_game_json(game, user_lat, user_lon, tz))
    return JSONResponse({"team": abv, "date": today_et().isoformat(), "games": games})


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/")
def root(request: Request):
    tz = get_user_tz(geolocate_ip(get_client_ip(request)))
    out, has_live = sched.render_smart_today(tz=tz)
    return respond(request, out, refresh_secs=30 if has_live else None)


@app.get("/tomorrow")
def tomorrow(request: Request):
    tz = get_user_tz(geolocate_ip(get_client_ip(request)))
    d = (today_et() + timedelta(days=1)).strftime("%Y-%m-%d")
    return respond(request, sched.render_schedule(d, tz=tz))


@app.get("/live")
def live(request: Request):
    tz = get_user_tz(geolocate_ip(get_client_ip(request)))
    return respond(request, sched.render_live(tz=tz), refresh_secs=30)


@app.get("/distance")
def distance(request: Request):
    ip = get_client_ip(request)
    geo = geolocate_ip(ip)
    if not geo:
        msg = (
            f"\n  {sched.YELLOW}Could not determine your location from IP {ip}.{sched.RESET}\n"
            f"  {sched.GRAY}Try from a non-VPN connection, or use /api/distance?lat=XX&lon=YY{sched.RESET}\n"
        )
        return respond(request, msg)
    city = f"{geo['city']}, {geo['region']}" if geo.get("region") else geo["city"]
    tz = get_user_tz(geo)
    return respond(request, sched.render_distance(geo["lat"], geo["lon"], city, tz=tz), refresh_secs=60)


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
    tz = get_user_tz(geolocate_ip(get_client_ip(request)))
    try:
        d = sched.parse_date(segment)
        out = sched.render_schedule(d.strftime("%Y-%m-%d"), tz=tz)
    except ValueError:
        out = sched.render_schedule(today_et().strftime("%Y-%m-%d"), segment.upper(), tz=tz)
    return respond(request, out)


@app.get("/tomorrow/{team}")
def tomorrow_team(request: Request, team: str):
    tz = get_user_tz(geolocate_ip(get_client_ip(request)))
    d = (today_et() + timedelta(days=1)).strftime("%Y-%m-%d")
    return respond(request, sched.render_schedule(d, team.upper(), tz=tz))


@app.get("/{team}/{date_str}")
def team_date(request: Request, team: str, date_str: str):
    tz = get_user_tz(geolocate_ip(get_client_ip(request)))
    try:
        d = sched.parse_date(date_str)
        out = sched.render_schedule(d.strftime("%Y-%m-%d"), team.upper(), tz=tz)
    except ValueError:
        out = f"Invalid date: {date_str}\n"
    return respond(request, out)
