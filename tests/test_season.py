"""Offline tests for the season-awareness helpers in mlbsched.py: where we are in
the baseball year, which season's stats and schedule to show, and what an empty
day says. MLB's calendar is stubbed with the real 2026/2027 dates from
statsapi.mlb.com/api/v1/seasons/<year>."""

import io
import os
import re
import tempfile
from datetime import date

import pytest
import requests

os.environ.setdefault("MLBSCHED_DATA_DIR", tempfile.mkdtemp(prefix="mlbsched-test-"))

from fastapi.testclient import TestClient   # noqa: E402

import mlbsched as sched                     # noqa: E402
import server                                # noqa: E402

ANSI = re.compile(r"\x1b\[[0-9;]*m")

SEASONS = {
    2026: {
        "seasonId": "2026",
        "springStartDate": "2026-02-20",
        "regularSeasonStartDate": "2026-03-25",
        "lastDate1stHalf": "2026-07-12",
        "firstDate2ndHalf": "2026-07-16",
        "regularSeasonEndDate": "2026-09-27",
        "postSeasonEndDate": "2026-10-31",
    },
    2027: {
        "seasonId": "2027",
        "springStartDate": "2027-02-19",
        "regularSeasonStartDate": "2027-03-25",
        "lastDate1stHalf": "2027-07-11",
        "firstDate2ndHalf": "2027-07-16",
        "regularSeasonEndDate": "2027-09-26",
        "postSeasonEndDate": "2027-10-31",
    },
}


@pytest.fixture
def calendar(monkeypatch):
    """MLB has published 2026 and 2027."""
    monkeypatch.setattr(sched, "season_dates", lambda year: SEASONS.get(year))


@pytest.fixture
def calendar_2026_only(monkeypatch):
    """Next season's calendar isn't out yet."""
    monkeypatch.setattr(sched, "season_dates", lambda year: SEASONS.get(year) if year == 2026 else None)


@pytest.fixture
def mlb_down(monkeypatch):
    monkeypatch.setattr(sched, "season_dates", lambda year: None)


# ── season_phase ──────────────────────────────────────────────────────────────

@pytest.mark.parametrize("today, phase", [
    (date(2026, 1, 15),  "preseason"),
    (date(2026, 2, 19),  "preseason"),
    (date(2026, 2, 20),  "spring"),       # first spring date
    (date(2026, 3, 24),  "spring"),
    (date(2026, 3, 25),  "regular"),      # Opening Day
    (date(2026, 7, 12),  "regular"),      # last day of the first half
    (date(2026, 7, 13),  "allstar"),
    (date(2026, 7, 15),  "allstar"),
    (date(2026, 7, 16),  "regular"),      # second half starts
    (date(2026, 9, 27),  "regular"),      # last regular-season day
    (date(2026, 9, 28),  "postseason"),
    (date(2026, 10, 31), "postseason"),   # last possible World Series date
    (date(2026, 11, 1),  "offseason"),
    (date(2026, 12, 31), "offseason"),
])
def test_season_phase(calendar, today, phase):
    assert sched.season_phase(today) == phase


def test_season_phase_falls_back_to_regular(mlb_down, monkeypatch):
    assert sched.season_phase(date(2026, 12, 25)) == "regular"
    # A published calendar missing every key we read also reads as "regular".
    monkeypatch.setattr(sched, "season_dates", lambda year: {"seasonId": str(year)})
    assert sched.season_phase(date(2026, 12, 25)) == "regular"


# ── stats_season / schedule_season ────────────────────────────────────────────

@pytest.mark.parametrize("today, season", [
    (date(2027, 1, 15), 2026),   # January: last season's numbers, not an empty board
    (date(2027, 3, 24), 2026),   # spring training still shows last season
    (date(2027, 3, 25), 2027),   # Opening Day flips it
    (date(2026, 11, 15), 2026),
])
def test_stats_season(calendar, today, season):
    assert sched.stats_season(today) == season


@pytest.mark.parametrize("today, season", [
    (date(2027, 3, 19), 2026),
    (date(2027, 3, 20), 2027),
])
def test_stats_season_calendar_fallback(mlb_down, today, season):
    assert sched.stats_season(today) == season


def test_schedule_season_flips_after_postseason(calendar):
    assert sched.schedule_season(date(2026, 10, 31)) == 2026
    assert sched.schedule_season(date(2026, 11, 1)) == 2027


def test_schedule_season_waits_for_next_calendar(calendar_2026_only):
    assert sched.schedule_season(date(2026, 11, 1)) == 2026


def test_schedule_season_mlb_down(mlb_down):
    assert sched.schedule_season(date(2026, 11, 15)) == 2026


# ── season_dates fetch + cache ────────────────────────────────────────────────

class _Resp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


def test_season_dates_fetch_cache_and_stale(monkeypatch):
    monkeypatch.setattr(sched, "_season_dates_cache", {})
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(url)
        year = int(url.rsplit("/", 1)[1])
        return _Resp({"seasons": [SEASONS[year]]} if year in SEASONS else {"seasons": []})
    monkeypatch.setattr(sched.requests, "get", fake_get)

    assert sched.season_dates(2026) == SEASONS[2026]
    assert sched.season_dates(2026) == SEASONS[2026]
    assert len(calls) == 1                                   # cached
    assert sched.season_dates(2031) is None                  # not published yet

    # Cache expired and MLB is down: serve what we had; nothing cached → None.
    fetched_at, info = sched._season_dates_cache[2026]
    sched._season_dates_cache[2026] = (fetched_at - sched._SEASON_TTL_SECONDS - 1, info)

    def down(url, params=None, timeout=None):
        raise requests.ConnectionError("down")
    monkeypatch.setattr(sched.requests, "get", down)
    assert sched.season_dates(2026) == SEASONS[2026]
    assert sched.season_dates(2028) is None


def test_season_date_parsing():
    assert sched.season_date(SEASONS[2026], "regularSeasonStartDate") == date(2026, 3, 25)
    assert sched.season_date(SEASONS[2026], "missingKey") is None
    assert sched.season_date(None, "regularSeasonStartDate") is None
    assert sched.season_date({"x": "not-a-date"}, "x") is None


# ── render_no_games_note ──────────────────────────────────────────────────────

def note(today: date) -> str:
    buf = io.StringIO()
    sched.render_no_games_note(today, buf)
    return ANSI.sub("", buf.getvalue())


def test_note_offseason(calendar):
    out = note(date(2026, 11, 15))
    assert "Offseason." in out
    assert "Spring training: Friday, February 19, 2027  (in 96 days)" in out
    assert "Opening Day 2027: Thursday, March 25, 2027  (in 130 days)" in out
    assert "2026 postseason: curl mlbsched.run/postseason/2026" in out


def test_note_preseason(calendar):
    out = note(date(2027, 1, 10))
    assert "Offseason." in out
    assert "Spring training: Friday, February 19, 2027  (in 40 days)" in out
    assert "Opening Day 2027: Thursday, March 25, 2027  (in 74 days)" in out
    assert "2026 postseason: curl mlbsched.run/postseason/2026" in out


def test_note_offseason_before_next_calendar(calendar_2026_only):
    out = note(date(2026, 11, 15))
    assert "Offseason." in out and "2026 postseason" in out
    assert "Spring training" not in out and "Opening Day" not in out


def test_note_countdown_tomorrow(calendar):
    assert "(tomorrow)" in note(date(2027, 2, 18))


@pytest.mark.parametrize("today, text", [
    (date(2026, 10, 9),  "Postseason off day. Bracket: curl mlbsched.run/postseason"),
    (date(2026, 7, 14),  "All-Star break. No games scheduled."),
    (date(2026, 3, 10),  "No games scheduled."),     # spring
    (date(2026, 6, 1),   "No games scheduled."),     # regular season
])
def test_note_in_season(calendar, today, text):
    out = note(today)
    assert text in out and "Offseason" not in out


def test_note_mlb_down(mlb_down):
    assert note(date(2026, 12, 25)).strip() == "No games scheduled."


# ── /api/season ───────────────────────────────────────────────────────────────

def test_api_season(calendar, monkeypatch):
    monkeypatch.setattr(server, "today_et", lambda: date(2026, 11, 15))
    with TestClient(server.app) as client:
        data = client.get("/api/season").json()
    assert data == {
        "date": "2026-11-15",
        "phase": "offseason",
        "stats_season": 2026,
        "schedule_season": 2027,
        "dates": SEASONS[2026],
    }
