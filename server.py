"""mlbsched web server — serves ANSI text to curl, HTML to browsers"""

import os
import re
import time
import html as _html
from pathlib import Path
from datetime import date, datetime, timedelta, timezone as _UTC
from fastapi import FastAPI, Request, Response
from fastapi.responses import PlainTextResponse, HTMLResponse, JSONResponse, FileResponse
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
import player
import lineup
import mascot
import broadcasts
import onthisday
import birthdays
import wp
import ical
import pitchers

app = FastAPI(docs_url=None, redoc_url=None)

@app.on_event("startup")
def startup():
    db.init_db()

_OG_IMAGE = Path(__file__).resolve().parent / "static" / "og.png"

# Paths skipped by request logging. These also skip the cache/Vary defaults below,
# so static + boilerplate responses keep whatever headers their handler set.
_NO_LOG_PATHS = {"/favicon.ico", "/robots.txt", "/og.png"}

@app.middleware("http")
async def log_requests(request: Request, call_next):
    path = request.url.path
    response = await call_next(request)
    if path in _NO_LOG_PATHS:
        return response
    try:
        ip = get_client_ip(request)
        ua = request.headers.get("user-agent", "")
        db.log_request(path, ip, ua)
    except Exception:
        pass
    # Responses vary by User-Agent (curl text vs browser HTML) and viewer timezone,
    # so they can't be shared across users — mark private + briefly cacheable per
    # client. /metrics is sensitive; never store it. Only fill in defaults a handler
    # didn't set explicitly.
    if "cache-control" not in response.headers:
        response.headers["Cache-Control"] = (
            "no-store" if path == "/metrics" else "private, max-age=30"
        )
    response.headers.setdefault("Vary", "User-Agent")
    return response

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.exception_handler(_requests.RequestException)
async def upstream_unavailable(request: Request, exc: _requests.RequestException):
    """Site-wide backstop: if any upstream (MLB, odds, weather, ip-api) fails and the
    handler didn't recover, degrade to a friendly 503 instead of a bare 500."""
    msg = "Upstream data source is temporarily unavailable. Please try again shortly.\n"
    headers = {"Retry-After": "30", "Cache-Control": "no-store"}
    if is_curl(request):
        return PlainTextResponse(msg, status_code=503, headers=headers)
    return HTMLResponse(html_wrap(f"\n  {msg}"), status_code=503, headers=headers)


def is_curl(request: Request) -> bool:
    ua = request.headers.get("user-agent", "").lower()
    return ua.startswith("curl")


def text(content: str):
    return PlainTextResponse(content)


# ANSI SGR code → CSS color. Palette matches the OG card + page chrome (GitHub dark)
# so terminal, browser, and social card all read as one product. Closed set — the
# renderer only emits these (see mlbsched.py); any other code is dropped.
_ANSI_FG = {
    "90":       "#6e7681",  # gray
    "91":       "#f85149",  # red
    "92":       "#3fb950",  # green
    "93":       "#d29922",  # yellow
    "94":       "#58a6ff",  # blue
    "96":       "#39c5cf",  # cyan
    "97":       "#e6edf3",  # white (default fg)
    "38;5;208": "#ff7b00",  # 256-color orange (accent)
}
_ANSI_RE = re.compile(r"\033\[([0-9;]*)m")


def ansi_to_html(content: str) -> str:
    """Translate the renderer's ANSI color codes into HTML-escaped colored spans."""
    out: list[str] = []
    color: str | None = None
    bold = dim = False

    def emit(chunk: str) -> None:
        if not chunk:
            return
        esc = _html.escape(chunk)
        styles = []
        if color:
            styles.append(f"color:{color}")
        if bold:
            styles.append("font-weight:bold")
        if dim:
            styles.append("opacity:0.6")
        out.append(f'<span style="{";".join(styles)}">{esc}</span>' if styles else esc)

    pos = 0
    for m in _ANSI_RE.finditer(content):
        emit(content[pos:m.start()])
        code = m.group(1)
        if code in ("", "0"):
            color, bold, dim = None, False, False
        elif code == "1":
            bold = True
        elif code == "2":
            dim = True
        elif code in _ANSI_FG:
            color = _ANSI_FG[code]
        pos = m.end()
    emit(content[pos:])
    return "".join(out)


def html_wrap(content: str, refresh_secs: int | None = None) -> str:
    clean = ansi_to_html(content)
    refresh_tag = f'<meta http-equiv="refresh" content="{refresh_secs}">' if refresh_secs else ""
    title = "mlbsched.run — MLB scores and schedule in your terminal"
    desc  = ("Live MLB scores, schedules, standings, odds, and 30+ commands — "
             "straight from your terminal with curl, or in the browser.")
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <meta name="description" content="{desc}">
  <meta property="og:type" content="website">
  <meta property="og:site_name" content="mlbsched.run">
  <meta property="og:title" content="{title}">
  <meta property="og:description" content="{desc}">
  <meta property="og:url" content="https://mlbsched.run/">
  <meta property="og:image" content="https://mlbsched.run/og.png">
  <meta property="og:image:width" content="1200">
  <meta property="og:image:height" content="630">
  <meta name="twitter:card" content="summary_large_image">
  <meta name="twitter:title" content="{title}">
  <meta name="twitter:description" content="{desc}">
  <meta name="twitter:image" content="https://mlbsched.run/og.png">
  {refresh_tag}
  <style>
    html   {{ color-scheme: dark; -webkit-text-size-adjust: 100%; text-size-adjust: 100%; }}
    body   {{ background: #0d1117; margin: 0; padding: 2rem; }}
    pre    {{ color: #e6edf3; font-family: 'Fira Mono', 'Courier New', monospace;
              font-size: 15px; line-height: 1.6; white-space: pre; margin: 0;
              overflow-x: auto; -webkit-overflow-scrolling: touch; }}
    a      {{ color: #58a6ff; }}
    footer {{ color: #6e7681; font-family: 'Fira Mono', 'Courier New', monospace;
              font-size: 12px; margin-top: 1.5rem; padding-left: 2px; }}
    footer a {{ color: #6e7681; text-decoration: none; }}
    footer a:hover {{ color: #58a6ff; }}
    /* Phones: trim padding and shrink the monospace grid (alignment preserved).
       These are the no-JS fallback; the fit() script below tunes the exact size
       so any view fits the viewport, with overflow-x as the last resort. */
    @media (max-width: 600px) {{
      body   {{ padding: 1rem 0.75rem; }}
      pre    {{ font-size: 13px; line-height: 1.5; }}
      footer {{ padding-left: 0.75rem; }}
    }}
    @media (max-width: 400px) {{
      pre    {{ font-size: 11px; }}
    }}
  </style>
</head>
<body>
<pre>{clean}</pre>
<footer>by Brian Pisano · <a href="https://www.brianpisano.com" target="_blank" rel="noopener">brianpisano.com</a></footer>
<script>
// Scale the monospace block down just enough that its widest line fits the
// screen, so fixed-width views read at a glance on phones without sideways
// scrolling. No-op when content already fits (e.g. desktop). Floor at 8px;
// below that, CSS overflow-x lets the user scroll instead.
(function () {{
  var pre = document.querySelector('pre');
  if (!pre) return;
  function fit() {{
    pre.style.fontSize = '';                       // reset to CSS/media-query size
    var base = parseFloat(getComputedStyle(pre).fontSize);
    var bs = getComputedStyle(document.body);
    var avail = document.documentElement.clientWidth
              - parseFloat(bs.paddingLeft) - parseFloat(bs.paddingRight);
    if (pre.scrollWidth > avail) {{
      pre.style.fontSize = Math.max(8, base * avail / pre.scrollWidth) + 'px';
    }}
  }}
  fit();
  addEventListener('resize', fit);
}})();
</script>
</body>
</html>"""


def respond(request: Request, content: str, refresh_secs: int | None = None, status_code: int = 200):
    if is_curl(request):
        return PlainTextResponse(content, status_code=status_code)
    return HTMLResponse(html_wrap(content, refresh_secs), status_code=status_code)


# ── IP geolocation ────────────────────────────────────────────────────────────

def get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host


def _geo_lookup(ip: str) -> dict | None:
    """Uncached network call to ip-api. Returns geo dict or None on failure/private IP."""
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


# In-process TTL cache over ip-api lookups. The free tier rate-limits at ~45 req/min,
# so under a traffic spike every uncached request would otherwise burn quota and stall.
# Successes live 6h (a viewer's location is stable); failures live 60s so a transient
# rate-limit or outage doesn't pin a client to "unknown" for hours.
_GEO_TTL_OK   = 6 * 60 * 60
_GEO_TTL_FAIL = 60
_geo_cache: dict[str, tuple[float, dict | None]] = {}   # ip -> (expires_at_monotonic, geo)


def geolocate_ip(ip: str) -> dict | None:
    """Returns {lat, lon, city, region, country, timezone} or None on failure/private IP."""
    now = time.monotonic()
    cached = _geo_cache.get(ip)
    if cached is not None and now < cached[0]:
        return cached[1]

    geo = _geo_lookup(ip)
    if len(_geo_cache) > 10_000:        # drop expired entries before they accumulate
        for k, (exp, _) in list(_geo_cache.items()):
            if now >= exp:
                del _geo_cache[k]
    _geo_cache[ip] = (now + (_GEO_TTL_OK if geo else _GEO_TTL_FAIL), geo)
    return geo


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


@app.get("/api/lineup/{team}")
def api_lineup(team: str):
    data = lineup.build_lineup_json(team)
    if "error" in data:
        return JSONResponse(data, status_code=404)
    return JSONResponse(data)


@app.get("/api/player/{name}")
def api_player(name: str):
    return JSONResponse(player.build_player_json(name))


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


@app.get("/api/random")
def api_random():
    return JSONResponse(mascot.build_random_json())


@app.get("/api/today")
def api_today(request: Request):
    geo = geolocate_ip(get_client_ip(request))
    user_lat = geo["lat"] if geo else None
    user_lon = geo["lon"] if geo else None
    tz = get_user_tz(geo)
    data = sched.fetch_schedule(today_et().strftime("%Y-%m-%d"))
    games = []
    for date_block in data.get("dates", []):
        for game in date_block.get("games", []):
            games.append(build_game_json(game, user_lat, user_lon, tz))
    return JSONResponse({"date": today_et().isoformat(), "games": games})


@app.get("/api/onthisday")
def api_onthisday():
    return JSONResponse(onthisday.build_onthisday_json())


@app.get("/api/pitchers")
def api_pitchers(request: Request):
    tz = get_user_tz(geolocate_ip(get_client_ip(request)))
    return JSONResponse(pitchers.build_pitchers_json(tz=tz))


@app.get("/api/birthdays/all")
def api_birthdays_alltime():
    return JSONResponse(birthdays.build_birthdays_alltime_json())


@app.get("/api/birthdays")
def api_birthdays():
    return JSONResponse(birthdays.build_birthdays_json())


@app.get("/api/broadcasts")
def api_broadcasts(request: Request):
    tz = get_user_tz(geolocate_ip(get_client_ip(request)))
    return JSONResponse(broadcasts.build_broadcasts_json(tz=tz))


@app.get("/api/broadcasts/{team}")
def api_broadcasts_team(request: Request, team: str):
    tz = get_user_tz(geolocate_ip(get_client_ip(request)))
    data = broadcasts.build_broadcasts_json(team_abv=team, tz=tz)
    if "error" in data:
        return JSONResponse(data, status_code=404)
    return JSONResponse(data)


@app.get("/api/wp/{team}")
def api_wp(team: str):
    d = (today_et() - timedelta(days=1)).strftime("%Y-%m-%d")
    return JSONResponse(wp.build_wp_json(team, d))


@app.get("/api/wp/{team}/{date_str}")
def api_wp_date(team: str, date_str: str):
    try:
        d = sched.parse_date(date_str)
    except ValueError:
        return JSONResponse({"error": f"Invalid date: {date_str}"}, status_code=400)
    return JSONResponse(wp.build_wp_json(team, d.strftime("%Y-%m-%d")))


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

_ROBOTS_TXT = "User-agent: *\nDisallow: /metrics\nDisallow: /api/\nAllow: /\n"


@app.get("/robots.txt")
def robots_txt():
    return PlainTextResponse(_ROBOTS_TXT)


@app.get("/favicon.ico")
def favicon():
    return Response(status_code=204)


@app.get("/og.png")
def og_image():
    if not _OG_IMAGE.is_file():
        return Response(status_code=404)
    return FileResponse(
        _OG_IMAGE,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=604800, immutable"},
    )


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


_BOT_PATH_PATTERNS = (
    "%wp-%",          # /wp-admin, /wp-includes, /wp-login
    "%xmlrpc%",       # /xmlrpc.php
    "%wlwmanifest%",  # Windows Live Writer probe
    "%phpmyadmin%",
    "%.php%",         # any PHP probe (app serves no PHP)
    "%.env%",         # .env scans at root or nested (/app/.env, /config/.env, …)
    "%/.git%",        # git config / HEAD scans
    "%/.aws%",
    "%/.vscode%",
    "%/.idea%",
    "%_profiler%",    # Symfony profiler probes
    "%phpinfo%",
    "%.json",         # credentials/secrets/service-account scans (app serves none)
    "%.zip",          # /source.zip, /backup.zip dumps
    "%.sql",
    "%.bak",
)
_BOT_FILTER_SQL = " AND " + " AND ".join(f"path NOT LIKE '{p}'" for p in _BOT_PATH_PATTERNS)
_BOT_MATCH_SQL  = " AND (" + " OR ".join(f"path LIKE '{p}'" for p in _BOT_PATH_PATTERNS) + ")"


@app.get("/metrics")
def metrics(request: Request, days: int = 30):
    expected = os.getenv("MLBSCHED_METRICS_TOKEN")
    if not expected:
        return PlainTextResponse(
            "/metrics requires MLBSCHED_METRICS_TOKEN to be configured\n",
            status_code=503,
        )
    auth = request.headers.get("authorization", "")
    if not (auth.startswith("Bearer ") and auth[7:] == expected):
        return PlainTextResponse(
            "unauthorized — pass Authorization: Bearer <token>\n",
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )
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

    top_paths = db.query(f"""
        SELECT path, COUNT(*) AS total, COUNT(DISTINCT ip_hash) AS uniq
        FROM   requests
        WHERE  date >= date('now', ?)
               {_BOT_FILTER_SQL}
        GROUP  BY path
        ORDER  BY total DESC
        LIMIT  50
    """, (f"-{days} days",))

    bot_paths = db.query(f"""
        SELECT path, COUNT(*) AS total, COUNT(DISTINCT ip_hash) AS uniq
        FROM   requests
        WHERE  date >= date('now', ?)
               {_BOT_MATCH_SQL}
        GROUP  BY path
        ORDER  BY total DESC
        LIMIT  15
    """, (f"-{days} days",))

    total_row = db.query("""
        SELECT COUNT(*) AS total, COUNT(DISTINCT ip_hash) AS uniq
        FROM   requests
        WHERE  date >= date('now', ?)
    """, (f"-{days} days",))[0]

    bot_total_row = db.query(f"""
        SELECT COUNT(*) AS total
        FROM   requests
        WHERE  date >= date('now', ?)
               {_BOT_MATCH_SQL}
    """, (f"-{days} days",))[0]
    real_total = total_row["total"] - bot_total_row["total"]

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
        bots_html = "\n".join(
            f"  <tr><td>{r['path']}</td><td>{r['total']}</td><td>{r['uniq']}</td></tr>"
            for r in bot_paths
        )
        html = f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>mlbsched metrics</title>
  <style>
    body  {{ background:#0d1117; color:#e6edf3; font-family:'Fira Mono','Courier New',monospace;
             font-size:14px; padding:2rem; margin:0; }}
    h2    {{ color:#58a6ff; margin-top:2rem; }}
    table {{ border-collapse:collapse; width:100%; max-width:700px; }}
    th    {{ color:#8b949e; text-align:left; padding:4px 12px 4px 0; border-bottom:1px solid #30363d; }}
    td    {{ padding:4px 12px 4px 0; border-bottom:1px solid #21262d; }}
    .sum  {{ color:#3fb950; font-weight:bold; }}
    .dim  {{ color:#6e7681; }}
  </style>
</head>
<body>
  <h2>mlbsched.run — last {days} days</h2>
  <p class="sum">Total: {total_row['total']} &nbsp;|&nbsp; Real: {real_total} &nbsp;|&nbsp; <span class="dim">Bots: {bot_total_row['total']}</span> &nbsp;|&nbsp; Unique IPs: {total_row['uniq']}</p>
  <h2>Daily breakdown</h2>
  <table>
    <tr><th>Date</th><th>Requests</th><th>Unique IPs</th><th>curl</th><th>Browser</th></tr>
    {rows_html}
  </table>
  <h2>Top paths <span class="dim" style="font-size:12px">(bot scanners filtered)</span></h2>
  <table>
    <tr><th>Path</th><th>Requests</th><th>Unique IPs</th></tr>
    {paths_html}
  </table>
  <h2 class="dim">Bot scanner noise</h2>
  <table>
    <tr><th class="dim">Path</th><th class="dim">Requests</th><th class="dim">Unique IPs</th></tr>
    {bots_html}
  </table>
  <p style="color:#8b949e;margin-top:2rem">?days=N to change range (max 365) &nbsp;|&nbsp; raw data: metrics.db (SQLite)</p>
</body>
</html>"""
        return HTMLResponse(html)

    lines = [f"mlbsched metrics — last {days} days", ""]
    lines.append(f"Total requests : {total_row['total']}  (real: {real_total}, bots: {bot_total_row['total']})")
    lines.append(f"Unique IPs     : {total_row['uniq']}")
    lines.append("")
    lines.append(f"{'Date':<12} {'Requests':>9} {'Unique':>7} {'curl':>6} {'Browser':>8}")
    lines.append("-" * 46)
    for r in daily:
        lines.append(f"{r['date']:<12} {r['total']:>9} {r['uniq']:>7} {r['curl_ct']:>6} {r['browser_ct']:>8}")
    lines.append("")
    lines.append("Top paths (bot scanners filtered)")
    lines.append(f"{'Path':<40} {'Requests':>9} {'Unique':>7}")
    lines.append("-" * 58)
    for r in top_paths:
        lines.append(f"{r['path']:<40} {r['total']:>9} {r['uniq']:>7}")
    lines.append("")
    lines.append("Bot scanner noise")
    lines.append(f"{'Path':<40} {'Requests':>9} {'Unique':>7}")
    lines.append("-" * 58)
    for r in bot_paths:
        lines.append(f"{r['path']:<40} {r['total']:>9} {r['uniq']:>7}")
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


@app.get("/lineup/{team}")
def lineup_route(request: Request, team: str):
    tz = get_user_tz(geolocate_ip(get_client_ip(request)))
    return respond(request, lineup.render_lineup(team, tz=tz))


@app.get("/player/{name}")
def player_route(request: Request, name: str):
    return respond(request, player.render_player(name))


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


@app.get("/random")
def random_route(request: Request):
    return respond(request, mascot.render_random())


@app.get("/today")
def today_route(request: Request):
    tz = get_user_tz(geolocate_ip(get_client_ip(request)))
    return respond(request, sched.render_schedule(today_et().strftime("%Y-%m-%d"), tz=tz))


@app.get("/onthisday")
def onthisday_route(request: Request):
    return respond(request, onthisday.render_onthisday())


@app.get("/pitchers")
def pitchers_route(request: Request):
    tz = get_user_tz(geolocate_ip(get_client_ip(request)))
    return respond(request, pitchers.render_pitchers(tz=tz))


@app.get("/birthdays/all")
def birthdays_alltime_route(request: Request):
    return respond(request, birthdays.render_birthdays_alltime())


@app.get("/birthdays")
def birthdays_route(request: Request):
    return respond(request, birthdays.render_birthdays())


@app.get("/broadcasts")
def broadcasts_today(request: Request):
    tz = get_user_tz(geolocate_ip(get_client_ip(request)))
    return respond(request, broadcasts.render_broadcasts(tz=tz))


@app.get("/broadcasts/{team}")
def broadcasts_team(request: Request, team: str):
    tz = get_user_tz(geolocate_ip(get_client_ip(request)))
    return respond(request, broadcasts.render_broadcasts(team_abv=team, tz=tz))


@app.get("/ical")
def ical_index(request: Request):
    return respond(request, ical.render_index())


@app.get("/ical/{filename}")
def ical_feed(filename: str):
    # Accept /ical/NYM.ics (calendar apps want the extension) and /ical/NYM.
    abv = filename.upper()
    if abv.endswith(".ICS"):
        abv = abv[:-4]
    body = ical.render_ical(abv)
    if body is None:
        return PlainTextResponse(f"Unknown team: {abv}  —  try: curl mlbsched.run/ical\n", status_code=404)
    return Response(
        content=body,
        media_type="text/calendar; charset=utf-8",
        headers={
            "Content-Disposition": f'inline; filename="{abv}.ics"',
            # Identical for all viewers (UTC times); safe to share at the edge.
            "Cache-Control": "public, max-age=1800",
            "Vary": "Accept-Encoding",
        },
    )


@app.get("/wp/{team}")
def wp_route(request: Request, team: str):
    tz = get_user_tz(geolocate_ip(get_client_ip(request)))
    d = (today_et() - timedelta(days=1)).strftime("%Y-%m-%d")
    return respond(request, wp.render_wp(team, d, tz=tz))


@app.get("/wp/{team}/{date_str}")
def wp_route_date(request: Request, team: str, date_str: str):
    tz = get_user_tz(geolocate_ip(get_client_ip(request)))
    try:
        d = sched.parse_date(date_str)
    except ValueError:
        return respond(request, f"\n  {sched.RED}Invalid date: {date_str}{sched.RESET}\n")
    return respond(request, wp.render_wp(team, d.strftime("%Y-%m-%d"), tz=tz))


@app.get("/{segment}")
def one_segment(request: Request, segment: str):
    tz = get_user_tz(geolocate_ip(get_client_ip(request)))
    try:
        d = sched.parse_date(segment)
        return respond(request, sched.render_schedule(d.strftime("%Y-%m-%d"), tz=tz))
    except ValueError:
        pass
    abv = segment.upper()
    if abv in sched.TEAMS:
        return respond(request, sched.render_schedule(today_et().strftime("%Y-%m-%d"), abv, tz=tz))
    msg = (
        f"\n  {sched.RED}Unknown: {segment}{sched.RESET}\n"
        f"  {sched.GRAY}Try: curl mlbsched.run/teams{sched.RESET}\n"
        f"  {sched.GRAY}     curl mlbsched.run/help{sched.RESET}\n"
    )
    return respond(request, msg, status_code=404)


@app.get("/yesterday/{team}")
def yesterday_team(request: Request, team: str):
    tz = get_user_tz(geolocate_ip(get_client_ip(request)))
    d = (today_et() - timedelta(days=1)).strftime("%Y-%m-%d")
    return respond(request, sched.render_team_recap(d, team.upper(), tz=tz, extra_per_game=wp.render_wp_for_game))


@app.get("/box/{team}")
def box_team(request: Request, team: str):
    tz = get_user_tz(geolocate_ip(get_client_ip(request)))
    d = (today_et() - timedelta(days=1)).strftime("%Y-%m-%d")
    return respond(request, sched.render_team_recap(d, team.upper(), tz=tz, extra_per_game=wp.render_wp_for_game))


@app.get("/box/{team}/{date_str}")
def box_team_date(request: Request, team: str, date_str: str):
    tz = get_user_tz(geolocate_ip(get_client_ip(request)))
    if date_str.lower() == "random":
        d_str = sched.random_recap_date(team.upper())
        if not d_str:
            msg = (
                f"\n  {sched.RED}Couldn't find a game for {team.upper()}{sched.RESET}\n"
                f"  {sched.GRAY}Try: curl mlbsched.run/teams{sched.RESET}\n"
            )
            return respond(request, msg, status_code=404)
        return respond(request, sched.render_team_recap(d_str, team.upper(), tz=tz, extra_per_game=wp.render_wp_for_game))
    try:
        d = sched.parse_date(date_str)
    except ValueError:
        return respond(request, f"\n  {sched.RED}Invalid date: {date_str}{sched.RESET}\n")
    return respond(request, sched.render_team_recap(d.strftime("%Y-%m-%d"), team.upper(), tz=tz, extra_per_game=wp.render_wp_for_game))


@app.get("/tomorrow/{team}")
def tomorrow_team(request: Request, team: str):
    tz = get_user_tz(geolocate_ip(get_client_ip(request)))
    d = (today_et() + timedelta(days=1)).strftime("%Y-%m-%d")
    return respond(request, sched.render_schedule(d, team.upper(), tz=tz))


@app.get("/{team}/{date_str}")
def team_date(request: Request, team: str, date_str: str):
    tz = get_user_tz(geolocate_ip(get_client_ip(request)))
    abv = team.upper()
    if abv not in sched.TEAMS:
        msg = (
            f"\n  {sched.RED}Unknown team: {abv}{sched.RESET}\n"
            f"  {sched.GRAY}Try: curl mlbsched.run/teams{sched.RESET}\n"
        )
        return respond(request, msg, status_code=404)
    try:
        d = sched.parse_date(date_str)
    except ValueError:
        msg = f"\n  {sched.RED}Invalid date: {date_str}{sched.RESET}\n"
        return respond(request, msg, status_code=404)
    return respond(request, sched.render_team_recap(d.strftime("%Y-%m-%d"), abv, tz=tz, extra_per_game=wp.render_wp_for_game))
