"""iCal (RFC 5545) calendar feed for a team's full-season schedule.

`/ical/<TEAM>.ics` returns a VCALENDAR that calendar apps can *subscribe* to,
so a team's games appear automatically and refresh on their own. One VEVENT per
game, with UID keyed on the MLB gamePk so a rescheduled game updates in place
instead of duplicating. Times are emitted in UTC; the calendar client converts
to the viewer's local zone, so the feed is identical for everyone and safely
shareable in a cache.

Calendar apps treat a subscribed feed as the whole truth: an event that drops out
of the feed is deleted from the subscriber's calendar. So the feed carries last
season alongside next season through the winter (see feed_seasons), and an MLB
outage answers 503 rather than an empty calendar."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import requests

from mlbsched import MLB_API, TEAMS, schedule_season, stats_season, today_et

# Baseball has no fixed end; block out a sensible window so the event reads well.
_GAME_DURATION = timedelta(hours=3)

# Regular season + postseason (wild card, division series, LCS, World Series).
# Deliberately excludes spring (S), which the schedule API also reports as Final.
_GAME_TYPES = "R,F,D,L,W"


def _fetch_season_games(team_id: int, year: int) -> list[dict]:
    resp = requests.get(
        f"{MLB_API}/schedule",
        params={
            "sportId": 1,
            "teamId": team_id,
            "season": year,
            "gameType": _GAME_TYPES,
            "hydrate": "venue(location)",
        },
        timeout=10,
    )
    resp.raise_for_status()
    games: list[dict] = []
    for d in resp.json().get("dates", []):
        games.extend(d.get("games", []))
    return games


# (team_id, season) -> games. A season whose postseason is over never changes, so
# it's fetched once per process. The live season is refetched on every request,
# and its last good copy is what an MLB hiccup gets served instead.
_season_games_cache: dict[tuple[int, int], list[dict]] = {}


def feed_seasons(today: date | None = None) -> list[int]:
    """The seasons a subscriber's calendar should hold: the one being played (or
    next up), plus last season until the new one's Opening Day.

    In season that's one year. Once MLB's postseason window closes,
    schedule_season() moves to next year while stats_season() stays on the one
    just played, so through the winter the feed has both; on Opening Day
    stats_season() catches up and last season drops out. Flipping straight to next
    year would wipe every subscriber's season from their calendar overnight."""
    today = today or today_et()
    return sorted({stats_season(today), schedule_season(today)})


def _season_games(team_id: int, year: int, finished: bool) -> list[dict]:
    """A team's games for one season. Raises requests.RequestException only when
    MLB is down and there's no earlier copy to fall back on."""
    key = (team_id, year)
    if finished and _season_games_cache.get(key):
        return _season_games_cache[key]
    try:
        games = _fetch_season_games(team_id, year)
    except requests.RequestException:
        if key in _season_games_cache:
            return _season_games_cache[key]
        raise
    _season_games_cache[key] = games
    return games


def _ics_escape(text: str) -> str:
    """Escape a TEXT value per RFC 5545 §3.3.11."""
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def _fold(line: str) -> str:
    """Fold a content line to <=75 octets, continuations starting with a space
    (RFC 5545 §3.1). Folds on character boundaries so multibyte chars survive."""
    if len(line.encode("utf-8")) <= 75:
        return line
    chunks: list[bytes] = []
    cur = b""
    for ch in line:
        b = ch.encode("utf-8")
        if len(cur) + len(b) > 75:
            chunks.append(cur)
            cur = b" " + b  # continuation line begins with one space
        else:
            cur += b
    chunks.append(cur)
    return "\r\n".join(c.decode("utf-8") for c in chunks)


def _parse_utc(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        return datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def _vstatus(detailed: str, tbd: bool) -> str:
    low = detailed.lower()
    if any(w in low for w in ("postpone", "cancel", "suspend")):
        return "CANCELLED"
    return "TENTATIVE" if tbd else "CONFIRMED"


def _one_per_game(games: list[dict]) -> list[dict]:
    """One entry per gamePk, since each becomes a UID and RFC 5545 wants them unique.

    MLB lists a postponed (or suspended) game twice under the same gamePk: the
    rained-out entry on the original date and the makeup on the new one. Keep the
    entry that's actually played; if every entry was called off, keep the latest.
    Order follows MLB's (by date)."""
    def rank(g: dict) -> tuple[bool, str]:
        detailed = (g.get("status") or {}).get("detailedState", "")
        return _vstatus(detailed, False) != "CANCELLED", g.get("gameDate") or ""

    best: dict = {}
    for g in games:
        key = g.get("gamePk") or id(g)
        if key not in best or rank(g) >= rank(best[key]):
            best[key] = g
    keep = {id(g) for g in best.values()}
    return [g for g in games if id(g) in keep]


def _location(venue: dict) -> str:
    name = venue.get("name", "")
    loc = venue.get("location", {}) or {}
    parts = [p for p in (name, loc.get("city"), loc.get("stateAbbrev") or loc.get("state")) if p]
    return ", ".join(parts)


def _describe(game: dict, away: str, home: str, status: dict, detailed: str) -> str:
    teams = game.get("teams", {})
    a = teams.get("away", {}).get("score")
    h = teams.get("home", {}).get("score")
    bits: list[str] = []
    if status.get("abstractGameState") == "Final" and a is not None and h is not None:
        bits.append(f"Final: {away} {a}, {home} {h}")
    elif detailed:
        bits.append(detailed)
    series = game.get("seriesDescription", "")
    if series and game.get("gameType") != "R":  # name the postseason round
        bits.append(series)
    return " · ".join(bits)


def _event(game: dict, abv: str, dtstamp: str) -> list[str]:
    """The VEVENT property lines for one game (unfolded)."""
    game_pk = game.get("gamePk")
    teams = game.get("teams", {})
    away = teams.get("away", {}).get("team", {}).get("name", "Away")
    home = teams.get("home", {}).get("team", {}).get("name", "Home")
    status = game.get("status", {}) or {}
    detailed = status.get("detailedState", "")
    tbd = bool(status.get("startTimeTBD"))
    official = game.get("officialDate") or (game.get("gameDate", "") or "")[:10]

    out = [
        "BEGIN:VEVENT",
        f"UID:mlb-{game_pk}@mlbsched.run",
        f"DTSTAMP:{dtstamp}",
    ]

    start = _parse_utc(game.get("gameDate"))
    if tbd or start is None:  # time unknown → all-day event on the official date
        d = official.replace("-", "")
        nxt = (datetime.strptime(d, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")
        out.append(f"DTSTART;VALUE=DATE:{d}")
        out.append(f"DTEND;VALUE=DATE:{nxt}")
    else:
        out.append(f"DTSTART:{start.strftime('%Y%m%dT%H%M%SZ')}")
        out.append(f"DTEND:{(start + _GAME_DURATION).strftime('%Y%m%dT%H%M%SZ')}")

    out.append(f"SUMMARY:{_ics_escape(f'{away} @ {home}')}")
    desc = _describe(game, away, home, status, detailed)
    if desc:
        out.append(f"DESCRIPTION:{_ics_escape(desc)}")
    location = _location(game.get("venue", {}) or {})
    if location:
        out.append(f"LOCATION:{_ics_escape(location)}")
    if official:
        out.append(f"URL:https://mlbsched.run/box/{abv}/{official}")
    out.append(f"STATUS:{_vstatus(detailed, tbd)}")
    out.append("END:VEVENT")
    return out


def render_ical(team_abv: str, today: date | None = None) -> str | None:
    """The full VCALENDAR text for a team's schedule, or None for an unknown team.

    If MLB is unreachable and a season has never been fetched, the
    requests.RequestException propagates (the server answers 503). An empty
    calendar would tell every subscriber's app to delete the team's games, while a
    503 leaves what they already have in place until the next refresh."""
    abv = team_abv.upper()
    if abv not in TEAMS:
        return None
    team_id, full_name, _color = TEAMS[abv]
    today = today or today_et()
    years = feed_seasons(today)
    live = schedule_season(today)
    for key in [k for k in _season_games_cache if k[0] == team_id and k[1] not in years]:
        del _season_games_cache[key]   # a season that has left the feed
    games = _one_per_game(
        [g for y in years for g in _season_games(team_id, y, finished=y < live)]
    )
    dtstamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    span = f"{years[0]}–{years[-1]}" if len(years) > 1 else str(years[0])

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//mlbsched.run//Schedule//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_ics_escape(full_name)}",
        f"X-WR-CALDESC:{_ics_escape(f'{full_name} {span} schedule — mlbsched.run')}",
        "X-WR-TIMEZONE:UTC",
        "REFRESH-INTERVAL;VALUE=DURATION:PT12H",
        "X-PUBLISHED-TTL:PT12H",
    ]
    for g in games:
        lines.extend(_event(g, abv, dtstamp))
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(l) for l in lines) + "\r\n"


def render_index() -> str:
    """Help text for /ical — how to subscribe, plus the list of team feeds."""
    abvs = sorted(TEAMS)
    rows = [
        "    " + "   ".join(abvs[i:i + 8])
        for i in range(0, len(abvs), 8)
    ]
    return "\n".join(
        [
            "",
            "  Subscribe to a team's full schedule in any calendar app — games",
            "  appear automatically and refresh on their own. Over the winter the",
            "  feed holds last season and next; last season's games stay until",
            "  the new Opening Day.",
            "",
            "      https://mlbsched.run/ical/<TEAM>.ics",
            "",
            "  Apple Calendar   File -> New Calendar Subscription -> paste the URL",
            "  Google Calendar  Other calendars -> From URL -> paste the URL",
            "  One-click        webcal://mlbsched.run/ical/<TEAM>.ics",
            "",
            "  Teams:",
            *rows,
            "",
            "  Example:  webcal://mlbsched.run/ical/NYM.ics",
            "",
        ]
    )
