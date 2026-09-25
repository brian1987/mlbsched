"""Offline tests for the renderers and helpers. No network: anything that would
call MLB is monkeypatched with a synthetic payload."""

import io
import re
from datetime import date

import pytest

import mlbsched as sched
import bestbets
import broadcasts
import ical
import odds
import streaks
import wp

ANSI = re.compile(r"\x1b\[[0-9;]*m")


def plain(s: str) -> str:
    return ANSI.sub("", s)


# ── dates ─────────────────────────────────────────────────────────────────────

def test_parse_date_formats():
    assert sched.parse_date("2026-04-20") == date(2026, 4, 20)
    assert sched.parse_date("04/20/2026") == date(2026, 4, 20)
    assert sched.parse_date("04-20-2026") == date(2026, 4, 20)
    assert sched.parse_date("4/20").month == 4
    with pytest.raises(ValueError):
        sched.parse_date("random")
    with pytest.raises(ValueError):
        sched.parse_date("2026-13-45")


def test_fmt_game_time_et_and_tz():
    from zoneinfo import ZoneInfo
    assert sched.fmt_game_time("2026-09-25T23:10:00Z") == "7:10 PM EDT"
    assert sched.fmt_game_time("2026-09-25T23:10:00Z", ZoneInfo("America/Los_Angeles")) == "4:10 PM PDT"
    assert sched.fmt_game_time("") == ""
    assert sched.fmt_game_time("garbage") == ""


# ── standings helpers ─────────────────────────────────────────────────────────

def test_race_number_normalizes_absent_and_dash():
    assert sched.race_number(None) == "-"
    assert sched.race_number("") == "-"
    assert sched.race_number("-") == "-"
    assert sched.race_number("E") == "E"
    assert sched.race_number(3) == "3"


def _standings_payload(with_race: bool):
    def team(tid, name, w, l, pct, **extra):
        rec = {
            "team": {"id": tid, "name": name},
            "wins": w, "losses": l, "winningPercentage": pct, "gamesBack": "-",
            "runDifferential": 10,
            "records": {"splitRecords": [{"type": "lastTen", "wins": 6, "losses": 4}]},
        }
        rec.update(extra)
        return rec
    nym = team(121, "New York Mets", 90, 60, ".600", **({"magicNumber": "5"} if with_race else {}))
    atl = team(144, "Atlanta Braves", 80, 70, ".533", **({"eliminationNumber": "5"} if with_race else {}))
    return {"records": [{"division": {"name": "National League East"}, "league": {"id": 104},
                         "teamRecords": [nym, atl]}]}


def test_has_race_numbers():
    assert sched._has_race_numbers(_standings_payload(True))
    assert not sched._has_race_numbers(_standings_payload(False))


def test_render_standings_hides_race_columns_when_unpublished(monkeypatch):
    monkeypatch.setattr(sched, "fetch_standings", lambda: _standings_payload(False))
    out = plain(sched.render_standings())
    assert "M#" not in out and "E#" not in out
    assert "New York Mets" in out and "Atlanta Braves" in out


def test_render_standings_shows_race_columns(monkeypatch):
    monkeypatch.setattr(sched, "fetch_standings", lambda: _standings_payload(True))
    out = plain(sched.render_standings())
    assert "M#" in out and "E#" in out
    mets_line = next(l for l in out.splitlines() if "New York Mets" in l)
    assert mets_line.rstrip().endswith("5    -") or "   5 " in mets_line


def test_build_standings_json_shape(monkeypatch):
    monkeypatch.setattr(sched, "fetch_standings", lambda: _standings_payload(True))
    data = sched.build_standings_json()
    div = data["divisions"][0]
    assert div["division"] == "National League East"
    assert div["league"] == "National League"
    row = div["teams"][0]
    assert row["team"] == "NYM" and row["magic"] == "5" and row["elim"] == "-"


# ── schedule + boxscore ───────────────────────────────────────────────────────

def _game(status="Final", away=(121, 3), home=(144, 5), innings=None, detail=None, gd="2026-09-25T23:10:00Z"):
    abstract = status
    ls = {}
    if innings is not None:
        ls["innings"] = innings
        ls["teams"] = {
            "away": {"runs": away[1], "hits": 7, "errors": 0},
            "home": {"runs": home[1], "hits": 9, "errors": 1},
        }
    return {
        "gamePk": 1,
        "gameDate": gd,
        "status": {"abstractGameState": abstract, "detailedState": detail or abstract},
        "teams": {
            "away": {"team": {"id": away[0]}, "score": away[1] if status != "Preview" else None},
            "home": {"team": {"id": home[0]}, "score": home[1] if status != "Preview" else None},
        },
        "linescore": ls,
        "venue": {"name": "Truist Park"},
    }


def test_boxscore_renders_only_final_and_dashes_unplayed_half():
    innings = [
        {"num": 1, "away": {"runs": 1}, "home": {"runs": 0}},
        {"num": 9, "away": {"runs": 2}, "home": {}},          # home didn't bat
    ]
    out = plain(sched.render_boxscore(_game(innings=innings)))
    assert "  1  9   R  H  E" in out
    nym, atl = [l for l in out.splitlines() if l.strip().startswith(("NYM", "ATL"))]
    assert nym.split()[1:3] == ["1", "2"]
    assert atl.split()[1:3] == ["0", "-"]
    assert sched.render_boxscore(_game(status="Live", innings=innings)) == ""


def test_build_box_json_keeps_nulls_for_scheduled_game(monkeypatch):
    g = _game(status="Preview")
    monkeypatch.setattr(sched, "fetch_schedule", lambda d, t=None: {"dates": [{"games": [g]}]})
    data = sched.build_box_json("nym", "2026-09-25")
    game = data["games"][0]
    assert data["team"] == "NYM"
    assert game["totals"]["away"]["runs"] is None      # not 0: the game hasn't happened
    assert game["winner"] is None and game["innings"] == []
    assert sched.build_box_json("zzz", "2026-09-25") == {"error": "Unknown team: ZZZ"}


def test_build_box_json_winner_and_partial_inning(monkeypatch):
    innings = [{"num": 1, "away": {"runs": 0}, "home": {}}]
    g = _game(status="Final", innings=innings)
    monkeypatch.setattr(sched, "fetch_schedule", lambda d, t=None: {"dates": [{"games": [g]}]})
    game = sched.build_box_json("NYM", "2026-09-25")["games"][0]
    assert game["winner"] == "ATL"
    assert game["innings"][0] == {"num": 1, "away": 0, "home": None}


def test_game_line_states():
    buf = io.StringIO(); sched._render_game_line(_game(status="Preview"), buf)
    assert "7:10 PM EDT" in plain(buf.getvalue())
    buf = io.StringIO(); sched._render_game_line(_game(status="Final"), buf)
    assert "Final" in plain(buf.getvalue())
    buf = io.StringIO()
    sched._render_game_line(_game(status="Preview", detail="Postponed"), buf)
    assert "Postponed" in plain(buf.getvalue()) and "PM" not in plain(buf.getvalue())


def test_render_schedule_unknown_team_and_no_games(monkeypatch):
    monkeypatch.setattr(sched, "fetch_schedule", lambda d, t=None: {"dates": [], "totalGames": 0})
    assert "Unknown team: ZZZ" in plain(sched.render_schedule("2026-09-25", "zzz"))
    assert "No games scheduled" in plain(sched.render_schedule("2026-09-25"))


def test_fmt_pitcher():
    assert sched._fmt_pitcher({"fullName": "Kodai Senga", "_record": {"wins": 10, "losses": 5, "era": "2.95"}}) == "Kodai Senga (10-5, 2.95)"
    assert sched._fmt_pitcher({"fullName": "Kodai Senga"}) == "Kodai Senga"
    assert sched._fmt_pitcher({}) == "TBD"


def test_haversine_nyc_to_la():
    miles = sched.haversine(40.7128, -74.0060, 34.0522, -118.2437)
    assert 2430 < miles < 2460


def test_historical_team_names():
    assert sched.abv_from_team({"id": 119, "name": "Brooklyn Dodgers"}) == "BRO"
    assert sched.abv_from_team({"id": 119, "name": "Los Angeles Dodgers"}) == "LAD"
    assert sched.abv_from_id(99999) == "???"


# ── schedule cache ────────────────────────────────────────────────────────────

def test_fetch_schedule_caches_and_serves_stale(monkeypatch):
    import requests
    calls = []

    class Resp:
        def __init__(self, payload): self.payload = payload
        def raise_for_status(self): pass
        def json(self): return self.payload

    def fake_get(url, params=None, timeout=None):
        calls.append(url)
        if len(calls) > 1 and url.endswith("/schedule"):
            raise requests.ConnectionError("down")
        return Resp({"dates": [], "totalGames": 0} if url.endswith("/schedule") else {"people": []})

    monkeypatch.setattr(sched.requests, "get", fake_get)
    sched._sched_cache.clear()
    a = sched.fetch_schedule("2031-01-01")
    b = sched.fetch_schedule("2031-01-01")          # within TTL: no call
    assert a is b and len(calls) == 1
    sched._sched_cache[("2031-01-01", None)] = (0.0, a)   # expire it
    c = sched.fetch_schedule("2031-01-01")          # upstream fails → stale served
    assert c is a


# ── streaks ───────────────────────────────────────────────────────────────────

def test_streaks_from_standings(monkeypatch):
    payload = _standings_payload(False)
    nym, atl = payload["records"][0]["teamRecords"]
    nym["streak"] = {"streakType": "wins", "streakNumber": 6, "streakCode": "W6"}
    atl["streak"] = {"streakType": "losses", "streakNumber": 3, "streakCode": "L3"}
    monkeypatch.setattr(streaks, "fetch_standings", lambda: payload)
    winning, losing = streaks.get_streaks(4)
    assert [s["abv"] for s in winning] == ["NYM"] and losing == []
    assert winning[0]["quip"]
    out = plain(streaks.render_streaks(4))
    assert "W6" in out and "Hot" in out


def test_quips_cover_every_length():
    for n in (4, 5, 6, 8, 9, 20):
        assert streaks._pick_quip("wins", n, "ATL")
        assert streaks._pick_quip("losses", n, "NYM")


# ── win probability ───────────────────────────────────────────────────────────

def test_wp_helpers():
    assert wp._downsample([1, 2, 3, 4], 2) == [1.5, 3.5]
    assert wp._downsample([1, 2], 5) == [1, 2]
    assert wp._spark([0, 50, 100]) == "▁▅█"
    assert [wp._ordinal(n) for n in (1, 2, 3, 4, 11, 12, 13, 21)] == ["1st", "2nd", "3rd", "4th", "11th", "12th", "13th", "21st"]


def test_biggest_swing():
    plays = [
        {"homeTeamWinProbabilityAdded": 5, "about": {"halfInning": "top", "inning": 1}},
        {"homeTeamWinProbabilityAdded": -32.5, "about": {"halfInning": "bottom", "inning": 8},
         "result": {"description": "Lindor homers (30) to right."}},
    ]
    s = wp.find_biggest_swing(plays)
    assert s["inning"] == 8 and s["half"] == "bottom" and s["description"] == "Lindor homers (30) to right"
    assert wp.find_biggest_swing([{"homeTeamWinProbabilityAdded": 0.01}]) is None
    assert wp.find_biggest_swing([]) is None


# ── odds math ─────────────────────────────────────────────────────────────────

def test_odds_math_round_trips():
    assert bestbets.american_to_prob(100) == pytest.approx(0.5)
    assert bestbets.american_to_prob(-200) == pytest.approx(2 / 3)
    assert bestbets.prob_to_american(0.5) == 100
    assert bestbets.prob_to_american(2 / 3) == -200
    assert bestbets.ev_fraction(0.5, 100) == pytest.approx(0.0)
    assert bestbets.ev_fraction(0.6, 100) == pytest.approx(0.2)
    a, b = bestbets._no_vig_pair(0.55, 0.50)
    assert a + b == pytest.approx(1.0)


def test_best_prices_only_ny_books():
    event = {"bookmakers": [
        {"key": "fanduel", "markets": [{"key": "h2h", "outcomes": [{"name": "New York Mets", "price": 110}]}]},
        {"key": "pinnacle", "markets": [{"key": "h2h", "outcomes": [{"name": "New York Mets", "price": 150}]}]},
        {"key": "draftkings", "markets": [{"key": "h2h", "outcomes": [{"name": "New York Mets", "price": 120}]}]},
    ]}
    best = odds._best_prices(event)
    assert best["h2h"]["New York Mets"] == {"price": 120, "point": None, "book": "draftkings"}


# ── broadcasts ────────────────────────────────────────────────────────────────

def test_broadcast_feed_split_and_sponsor_strip():
    tv = [
        {"type": "TV", "language": "en", "isNational": True, "name": "ESPN Presented by Progressive"},
        {"type": "TV", "language": "en", "isNational": True, "name": "ESPN"},
        {"type": "TV", "language": "en", "isNational": False, "homeAway": "home", "name": "SNY"},
        {"type": "TV", "language": "es", "isNational": False, "homeAway": "away", "name": "Bally Español"},
    ]
    en = broadcasts._tv_english(tv)
    nationals, away, home = broadcasts._split_feeds(en)
    assert [broadcasts._clean_name(n["name"]) for n in nationals] == ["ESPN"]
    assert home["name"] == "SNY" and away is None


# ── ical ──────────────────────────────────────────────────────────────────────

def test_ical_escape_and_fold():
    assert ical._ics_escape("a,b;c\\d\ne") == "a\\,b\\;c\\\\d\\ne"
    long = "SUMMARY:" + "x" * 100
    folded = ical._fold(long)
    lines = folded.split("\r\n")
    assert all(len(l.encode()) <= 75 for l in lines) and lines[1].startswith(" ")
    assert "".join(l[1:] if i else l for i, l in enumerate(lines)) == long


def test_ical_event_tbd_becomes_all_day():
    g = {"gamePk": 7, "officialDate": "2026-04-20", "gameDate": "2026-04-20T07:33:00Z",
         "status": {"detailedState": "Scheduled", "startTimeTBD": True},
         "teams": {"away": {"team": {"name": "Atlanta Braves"}}, "home": {"team": {"name": "New York Mets"}}},
         "venue": {"name": "Citi Field", "location": {"city": "Queens", "stateAbbrev": "NY"}}}
    lines = ical._event(g, "NYM", "20260101T000000Z")
    assert "DTSTART;VALUE=DATE:20260420" in lines and "DTEND;VALUE=DATE:20260421" in lines
    assert "STATUS:TENTATIVE" in lines
    assert "LOCATION:Citi Field\\, Queens\\, NY" in lines
    assert "URL:https://mlbsched.run/box/NYM/2026-04-20" in lines
    g["status"]["startTimeTBD"] = False; g["gameDate"] = "2026-04-20T23:10:00Z"
    lines = ical._event(g, "NYM", "20260101T000000Z")
    assert "DTSTART:20260420T231000Z" in lines and "STATUS:CONFIRMED" in lines


def test_ical_calendar_is_valid_for_unknown_team(monkeypatch):
    assert ical.render_ical("ZZZ") is None
    monkeypatch.setattr(ical, "_fetch_season_games", lambda tid, y: [])
    cal = ical.render_ical("nym")
    assert cal.startswith("BEGIN:VCALENDAR\r\n") and cal.endswith("END:VCALENDAR\r\n")
    assert "X-WR-CALNAME:New York Mets" in cal
