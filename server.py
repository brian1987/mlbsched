"""mlbsched web server — serves ANSI text to curl, HTML to browsers"""

from datetime import date, datetime, timedelta, timezone as _UTC
from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import requests as _requests
import mlbsched as sched
from mlbsched import today_et
import db
import odds
import bestbets
import weather
import streaks
import leaders
import wildcard
import h2h

app = FastAPI(docs_url=None, redoc_url=None)

@app.on_event("startup")
def startup():
    db.init_db()

@app.middleware("http")
async def log_requests(request: Request, call_next):
    response = await call_next(request)
    try:
        ip = get_client_ip(request)
        ua = request.headers.get("user-agent", "")
        db.log_request(request.url.path, ip, ua)
    except Exception:
        pass
    return response

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
    reason    = game["status"].get("reason") or None
    linescore = game.get("linescore", {})

    game_time = sched.fmt_game_time(game.get("gameDate", ""), tz) or None

    loc = sched.game_location(game)
    stadium_name = loc[0] if loc else None
    stadium_lat  = loc[1] if loc else None
    stadium_lon  = loc[2] if loc else None

    distance_miles = None
    if user_lat is not None and user_lon is not None and stadium_lat is not None:
        distance_miles = round(sched.haversine(user_lat, user_lon, stadium_lat, stadium_lon), 1)

    def pitcher_json(pp: dict | None) -> dict | None:
        if not pp:
            return None
        rec = pp.get("_record") or {}
        return {
            "id":     pp.get("id"),
            "name":   pp.get("fullName"),
            "wins":   rec.get("wins"),
            "losses": rec.get("losses"),
            "era":    rec.get("era"),
        }

    return {
        "away": away_abv,
        "away_name": sched.TEAMS.get(away_abv, (None, away_abv, None))[1],
        "home": home_abv,
        "home_name": sched.TEAMS.get(home_abv, (None, home_abv, None))[1],
        "away_score": game["teams"]["away"].get("score"),
        "home_score": game["teams"]["home"].get("score"),
        "status": abstract,
        "detail": status,
        "reason": reason,
        "inning": linescore.get("currentInning"),
        "inning_half": linescore.get("inningHalf"),
        "game_time": game_time,
        "stadium": stadium_name,
        "stadium_lat": stadium_lat,
        "stadium_lon": stadium_lon,
        "distance_miles": distance_miles,
        "away_pitcher": pitcher_json(game["teams"]["away"].get("probablePitcher")),
        "home_pitcher": pitcher_json(game["teams"]["home"].get("probablePitcher")),
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


@app.get("/api/odds")
def api_odds(request: Request):
    tz = get_user_tz(geolocate_ip(get_client_ip(request)))
    events, meta = odds.get_odds_events()
    events = odds._filter_events_today(events, tz)
    return JSONResponse({
        "date":   today_et().isoformat(),
        "meta":   meta,
        "games":  [odds.build_odds_json(e, tz) for e in events],
    })


@app.get("/api/odds/{team}")
def api_odds_team(request: Request, team: str):
    abv = team.upper()
    if abv not in sched.TEAMS:
        return JSONResponse({"error": f"Unknown team: {abv}"}, status_code=404)
    tz = get_user_tz(geolocate_ip(get_client_ip(request)))
    events, meta = odds.get_odds_events()
    events = odds._filter_events_today(events, tz)
    events = [
        e for e in events
        if abv in (
            odds.FULL_NAME_TO_ABV.get(e.get("home_team", ""), ""),
            odds.FULL_NAME_TO_ABV.get(e.get("away_team", ""), ""),
        )
    ]
    return JSONResponse({
        "team":  abv,
        "date":  today_et().isoformat(),
        "meta":  meta,
        "games": [odds.build_odds_json(e, tz) for e in events],
    })


@app.get("/api/bestbets")
def api_bestbets(request: Request):
    tz = get_user_tz(geolocate_ip(get_client_ip(request)))
    events, meta = odds.get_odds_events()
    events = odds._filter_events_today(events, tz)
    events = bestbets._filter_upcoming(events)
    all_edges: list[dict] = []
    for e in events:
        all_edges.extend(bestbets.find_edges(e))
    top = sorted(all_edges, key=lambda x: -x["ev"])[:bestbets.TOP_N]
    return JSONResponse({
        "date":  today_et().isoformat(),
        "meta":  meta,
        "edges": [bestbets.build_edge_json(e) for e in top],
    })


@app.get("/api/bestbets/{team}")
def api_bestbets_team(request: Request, team: str):
    abv = team.upper()
    if abv not in sched.TEAMS:
        return JSONResponse({"error": f"Unknown team: {abv}"}, status_code=404)
    tz = get_user_tz(geolocate_ip(get_client_ip(request)))
    events, meta = odds.get_odds_events()
    events = odds._filter_events_today(events, tz)
    events = bestbets._filter_upcoming(events)
    events = [
        e for e in events
        if abv in (
            odds.FULL_NAME_TO_ABV.get(e.get("home_team", ""), ""),
            odds.FULL_NAME_TO_ABV.get(e.get("away_team", ""), ""),
        )
    ]
    all_edges: list[dict] = []
    for e in events:
        all_edges.extend(bestbets.find_edges(e))
    all_edges.sort(key=lambda x: -x["ev"])
    return JSONResponse({
        "team":  abv,
        "date":  today_et().isoformat(),
        "meta":  meta,
        "edges": [bestbets.build_edge_json(e) for e in all_edges],
    })


@app.get("/api/streaks")
def api_streaks(min: int = streaks.DEFAULT_MIN):
    min = max(1, min)
    winning, losing = streaks.get_streaks(min)
    return JSONResponse({
        "date":    today_et().isoformat(),
        "min":     min,
        "winning": [streaks.build_streak_json(s) for s in winning],
        "losing":  [streaks.build_streak_json(s) for s in losing],
    })


@app.get("/api/h2h/{team_a}/{team_b}")
def api_h2h(team_a: str, team_b: str):
    a = team_a.upper()
    b = team_b.upper()
    if a not in sched.TEAMS or b not in sched.TEAMS:
        bad = a if a not in sched.TEAMS else b
        return JSONResponse({"error": f"Unknown team: {bad}"}, status_code=404)
    if a == b:
        return JSONResponse({"error": "Pick two different teams."}, status_code=400)
    return JSONResponse(h2h.build_h2h_json(a, b))


@app.get("/api/wildcard")
def api_wildcard():
    return JSONResponse({"date": today_et().isoformat(), **wildcard.get_wildcard_json()})


@app.get("/api/leaders")
def api_leaders():
    out = []
    season = today_et().year
    for group_name, tiles in leaders.DASHBOARD:
        for alias, title, n in tiles:
            data = leaders.get_leaders(alias, n)
            if data:
                data["title"] = title
                out.append(data)
    return JSONResponse({"season": season, "leaders": out})


@app.get("/api/leaders/{stat}")
def api_leaders_stat(stat: str, limit: int = 25):
    limit = max(1, min(limit, 50))
    data = leaders.get_leaders(stat.lower(), limit)
    if data is None:
        return JSONResponse(
            {"error": f"Unknown stat: {stat}", "stats": leaders.list_stats()},
            status_code=404,
        )
    return JSONResponse(data)


@app.get("/api/weather")
def api_weather(request: Request):
    data = sched.fetch_schedule(today_et().strftime("%Y-%m-%d"))
    games_out = []
    for date_block in data.get("dates", []):
        for game in date_block.get("games", []):
            away_id = game["teams"]["away"]["team"]["id"]
            home_id = game["teams"]["home"]["team"]["id"]
            loc = weather.stadium_location(game)
            stadium = loc[0] if loc else None
            w = None
            if loc and not weather.is_indoor(game):
                _, lat, lon = loc
                w = weather.get_weather(lat, lon)
            games_out.append({
                "away":     sched.abv_from_id(away_id),
                "home":     sched.abv_from_id(home_id),
                "stadium":  stadium,
                "indoor":   weather.is_indoor(game),
                "weather":  w,
            })
    return JSONResponse({"date": today_et().isoformat(), "games": games_out})


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


@app.get("/yesterday")
def yesterday(request: Request):
    tz = get_user_tz(geolocate_ip(get_client_ip(request)))
    d = (today_et() - timedelta(days=1)).strftime("%Y-%m-%d")
    return respond(request, sched.render_schedule(d, tz=tz))


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


@app.get("/metrics")
def metrics(request: Request, days: int = 30):
    days = max(1, min(days, 365))

    daily = db.query("""
        SELECT date,
               COUNT(*)                              AS total,
               COUNT(DISTINCT ip_hash)               AS uniq,
               SUM(client = 'curl')                  AS curl_ct,
               SUM(client = 'browser')               AS browser_ct
        FROM   requests
        WHERE  date >= date('now', ?)
        GROUP  BY date
        ORDER  BY date DESC
    """, (f"-{days} days",))

    top_paths = db.query("""
        SELECT path, COUNT(*) AS total, COUNT(DISTINCT ip_hash) AS uniq
        FROM   requests
        WHERE  date >= date('now', ?)
        GROUP  BY path
        ORDER  BY total DESC
        LIMIT  15
    """, (f"-{days} days",))

    total_row = db.query("""
        SELECT COUNT(*) AS total, COUNT(DISTINCT ip_hash) AS uniq
        FROM   requests
        WHERE  date >= date('now', ?)
    """, (f"-{days} days",))[0]

    if not is_curl(request):
        rows_html = "\n".join(
            f"  <tr><td>{r['date']}</td><td>{r['total']}</td>"
            f"<td>{r['uniq']}</td><td>{r['curl_ct']}</td><td>{r['browser_ct']}</td></tr>"
            for r in daily
        )
        paths_html = "\n".join(
            f"  <tr><td>{r['path']}</td><td>{r['total']}</td><td>{r['uniq']}</td></tr>"
            for r in top_paths
        )
        html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>mlbsched metrics</title>
  <style>
    body  {{ background:#0d1117; color:#e6edf3; font-family:'Fira Mono','Courier New',monospace;
             font-size:14px; padding:2rem; margin:0; }}
    h2    {{ color:#58a6ff; margin-top:2rem; }}
    table {{ border-collapse:collapse; width:100%; max-width:700px; }}
    th    {{ color:#8b949e; text-align:left; padding:4px 12px 4px 0; border-bottom:1px solid #30363d; }}
    td    {{ padding:4px 12px 4px 0; border-bottom:1px solid #21262d; }}
    .sum  {{ color:#3fb950; font-weight:bold; }}
  </style>
</head>
<body>
  <h2>mlbsched.run — last {days} days</h2>
  <p class="sum">Total requests: {total_row['total']} &nbsp;|&nbsp; Unique IPs: {total_row['uniq']}</p>
  <h2>Daily breakdown</h2>
  <table>
    <tr><th>Date</th><th>Requests</th><th>Unique IPs</th><th>curl</th><th>Browser</th></tr>
    {rows_html}
  </table>
  <h2>Top paths</h2>
  <table>
    <tr><th>Path</th><th>Requests</th><th>Unique IPs</th></tr>
    {paths_html}
  </table>
  <p style="color:#8b949e;margin-top:2rem">?days=N to change range (max 365) &nbsp;|&nbsp; raw data: metrics.db (SQLite)</p>
</body>
</html>"""
        return HTMLResponse(html)

    lines = [f"mlbsched metrics — last {days} days", ""]
    lines.append(f"Total requests : {total_row['total']}")
    lines.append(f"Unique IPs     : {total_row['uniq']}")
    lines.append("")
    lines.append(f"{'Date':<12} {'Requests':>9} {'Unique':>7} {'curl':>6} {'Browser':>8}")
    lines.append("-" * 46)
    for r in daily:
        lines.append(f"{r['date']:<12} {r['total']:>9} {r['uniq']:>7} {r['curl_ct']:>6} {r['browser_ct']:>8}")
    lines.append("")
    lines.append(f"{'Path':<30} {'Requests':>9} {'Unique':>7}")
    lines.append("-" * 48)
    for r in top_paths:
        lines.append(f"{r['path']:<30} {r['total']:>9} {r['uniq']:>7}")
    return text("\n".join(lines) + "\n")


@app.get("/weather")
def weather_today(request: Request):
    return respond(request, weather.render_weather())


@app.get("/odds")
def odds_today(request: Request):
    tz = get_user_tz(geolocate_ip(get_client_ip(request)))
    return respond(request, odds.render_odds(tz=tz))


@app.get("/odds/{team}")
def odds_team(request: Request, team: str):
    tz = get_user_tz(geolocate_ip(get_client_ip(request)))
    abv = team.upper()
    if abv not in sched.TEAMS:
        return respond(request, f"\n  {sched.RED}Unknown team: {abv}{sched.RESET}\n  {sched.GRAY}Try: curl mlbsched.run/teams{sched.RESET}\n")
    return respond(request, odds.render_odds(team_abv=abv, tz=tz))


@app.get("/streaks")
def streaks_today(request: Request, min: int = streaks.DEFAULT_MIN):
    min = max(1, min)
    return respond(request, streaks.render_streaks(min_streak=min))


@app.get("/h2h/{team_a}/{team_b}")
def h2h_route(request: Request, team_a: str, team_b: str):
    tz = get_user_tz(geolocate_ip(get_client_ip(request)))
    return respond(request, h2h.render_h2h(team_a, team_b, tz=tz))


@app.get("/wildcard")
def wildcard_today(request: Request):
    return respond(request, wildcard.render_wildcard())


@app.get("/leaders")
def leaders_today(request: Request):
    return respond(request, leaders.render_leaders_dashboard())


@app.get("/leaders/{stat}")
def leaders_stat(request: Request, stat: str, limit: int = 25):
    limit = max(1, min(limit, 50))
    return respond(request, leaders.render_leaders_one(stat.lower(), count=limit))


@app.get("/bestbets")
def bestbets_today(request: Request):
    tz = get_user_tz(geolocate_ip(get_client_ip(request)))
    return respond(request, bestbets.render_bestbets(tz=tz))


@app.get("/bestbets/{team}")
def bestbets_team(request: Request, team: str):
    tz = get_user_tz(geolocate_ip(get_client_ip(request)))
    abv = team.upper()
    if abv not in sched.TEAMS:
        return respond(request, f"\n  {sched.RED}Unknown team: {abv}{sched.RESET}\n  {sched.GRAY}Try: curl mlbsched.run/teams{sched.RESET}\n")
    return respond(request, bestbets.render_bestbets(team_abv=abv, tz=tz))


@app.get("/{segment}")
def one_segment(request: Request, segment: str):
    tz = get_user_tz(geolocate_ip(get_client_ip(request)))
    try:
        d = sched.parse_date(segment)
        out = sched.render_schedule(d.strftime("%Y-%m-%d"), tz=tz)
    except ValueError:
        out = sched.render_schedule(today_et().strftime("%Y-%m-%d"), segment.upper(), tz=tz)
    return respond(request, out)


@app.get("/yesterday/{team}")
def yesterday_team(request: Request, team: str):
    tz = get_user_tz(geolocate_ip(get_client_ip(request)))
    d = (today_et() - timedelta(days=1)).strftime("%Y-%m-%d")
    return respond(request, sched.render_team_recap(d, team.upper(), tz=tz))


@app.get("/box/{team}")
def box_team(request: Request, team: str):
    tz = get_user_tz(geolocate_ip(get_client_ip(request)))
    d = (today_et() - timedelta(days=1)).strftime("%Y-%m-%d")
    return respond(request, sched.render_team_recap(d, team.upper(), tz=tz))


@app.get("/box/{team}/{date_str}")
def box_team_date(request: Request, team: str, date_str: str):
    tz = get_user_tz(geolocate_ip(get_client_ip(request)))
    try:
        d = sched.parse_date(date_str)
    except ValueError:
        return respond(request, f"\n  {sched.RED}Invalid date: {date_str}{sched.RESET}\n")
    return respond(request, sched.render_team_recap(d.strftime("%Y-%m-%d"), team.upper(), tz=tz))


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
        out = sched.render_team_recap(d.strftime("%Y-%m-%d"), team.upper(), tz=tz)
    except ValueError:
        out = f"Invalid date: {date_str}\n"
    return respond(request, out)
