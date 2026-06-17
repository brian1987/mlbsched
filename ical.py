"""iCal (RFC 5545) calendar feed for a team's full-season schedule.

`/ical/<TEAM>.ics` returns a VCALENDAR that calendar apps can *subscribe* to,
so a team's games appear automatically and refresh on their own. One VEVENT per
game, with UID keyed on the MLB gamePk so a rescheduled game updates in place
instead of duplicating. Times are emitted in UTC; the calendar client converts
to the viewer's local zone, so the feed is identical for everyone and safely
shareable in a cache."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import requests

from mlbsched import MLB_API, TEAMS, today_et

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


def render_ical(team_abv: str) -> str | None:
    """The full VCALENDAR text for a team's season, or None for an unknown team."""
    abv = team_abv.upper()
    if abv not in TEAMS:
        return None
    team_id, full_name, _color = TEAMS[abv]
    year = today_et().year
    try:
        games = _fetch_season_games(team_id, year)
    except requests.RequestException:
        games = []  # serve an empty-but-valid calendar rather than erroring the client
    dtstamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//mlbsched.run//Schedule//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{_ics_escape(full_name)}",
        f"X-WR-CALDESC:{_ics_escape(f'{full_name} {year} schedule — mlbsched.run')}",
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
            "  appear automatically and refresh on their own:",
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
