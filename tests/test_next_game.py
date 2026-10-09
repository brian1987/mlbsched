"""Next game past the season's end: Opening Day look-ahead for /<TEAM>/next and
/api/next/<TEAM>. Offline — MLB's schedule and season calendar are stubbed."""

import os
import re
import tempfile
from datetime import date

import pytest

# Point SQLite at a scratch dir before server (and db) import.
os.environ["MLBSCHED_DATA_DIR"] = tempfile.mkdtemp(prefix="mlbsched-test-")

from fastapi.testclient import TestClient   # noqa: E402

import mlbsched as sched                     # noqa: E402
import server                                # noqa: E402

ANSI = re.compile(r"\x1b\[[0-9;]*m")
TODAY = date(2026, 10, 9)
SEASONS = {
    2026: {"regularSeasonStartDate": "2026-03-25", "regularSeasonEndDate": "2026-09-27"},
    2027: {"regularSeasonStartDate": "2027-03-25", "regularSeasonEndDate": "2027-09-26"},
}


def _game(pk, official, gtype="R", away=121, home=146, tbd=True, state="Preview"):
    return {
        "gamePk": pk,
        "gameType": gtype,
        "officialDate": official,
        "gameDate": f"{official}T07:33:00Z" if tbd else f"{official}T20:10:00Z",
        "status": {"abstractGameState": state, "detailedState": "Scheduled", "startTimeTBD": tbd},
        "teams": {"away": {"team": {"id": away}}, "home": {"team": {"id": home}}},
        "linescore": {},
        "venue": {"name": "loanDepot park"},
    }


OPENER_2027 = _game(853317, "2027-03-25")


class _Resp:
    def __init__(self, payload): self.payload = payload
    def raise_for_status(self): pass
    def json(self): return self.payload


@pytest.fixture
def mlb(monkeypatch):
    """Stub MLB: `window` is what the 45-day next-game query returns, `openers`
    maps season year → the team's first regular-season game. Records each call."""
    state = {"window": [], "openers": {2027: OPENER_2027}, "calls": []}

    def fake_get(url, params=None, timeout=None):
        params = params or {}
        state["calls"].append(params.get("gameType", "window") if url.endswith("/schedule") else url)
        if url.endswith("/schedule"):
            if params.get("gameType") == "R":
                g = state["openers"].get(int(params["startDate"][:4]))
                return _Resp({"dates": [{"games": [g]}] if g else []})
            return _Resp({"dates": [{"games": state["window"]}] if state["window"] else []})
        return _Resp({"people": []})

    monkeypatch.setattr(sched.requests, "get", fake_get)
    monkeypatch.setattr(sched, "today_et", lambda: TODAY)
    monkeypatch.setattr(server, "today_et", lambda: TODAY)
    monkeypatch.setattr(sched, "season_dates", lambda y: SEASONS.get(y))
    monkeypatch.setattr(server, "geolocate_ip", lambda ip: None)
    sched._next_cache.clear()
    sched._opener_cache.clear()
    return state


def test_empty_window_falls_back_to_next_seasons_opener(mlb):
    game = sched.fetch_next_game(121)
    assert game["gamePk"] == OPENER_2027["gamePk"]
    assert sched.days_until(game) == 167
    assert sched.season_opener(121, game) is game


def test_no_opener_when_next_season_unpublished(mlb):
    mlb["openers"] = {}
    assert sched.fetch_next_game(121) is None
    out = ANSI.sub("", sched.render_next_game("NYM"))
    assert "No games scheduled in the next 45 days." in out


def test_in_season_game_skips_the_opener_lookup(mlb):
    midseason = _game(1, "2026-10-11", gtype="L", away=119, home=158, tbd=False)
    mlb["window"] = [midseason]
    assert sched.fetch_next_game(119) is midseason
    assert sched.season_opener(119, midseason) is None
    assert "R" not in mlb["calls"]          # no opener query for a postseason team


def test_spring_training_points_at_opening_day(mlb, monkeypatch):
    monkeypatch.setattr(sched, "today_et", lambda: date(2027, 2, 20))
    spring = _game(2, "2027-02-21", gtype="S", away=121, home=117, tbd=False)
    mlb["window"] = [spring]
    assert sched.fetch_next_game(121) is spring
    assert sched.season_opener(121, spring)["gamePk"] == OPENER_2027["gamePk"]
    out = ANSI.sub("", sched.render_next_game("NYM"))
    assert "Opening Day 2027: Thursday, March 25 @ MIA · in 33 days" in out


def test_opener_already_played_is_not_counted_down(mlb, monkeypatch):
    # Back home for exhibitions after an overseas opening series.
    monkeypatch.setattr(sched, "today_et", lambda: date(2027, 3, 23))
    mlb["openers"] = {2027: _game(3, "2027-03-18", away=119, home=112)}
    exhibition = _game(4, "2027-03-24", gtype="E", away=108, home=119, tbd=False)
    assert sched.season_opener(119, exhibition) is None


def test_opening_day_itself_is_flagged(mlb, monkeypatch):
    monkeypatch.setattr(sched, "today_et", lambda: date(2027, 3, 25))
    live_copy = dict(OPENER_2027)
    mlb["window"] = [live_copy]
    assert sched.season_opener(121, sched.fetch_next_game(121)) is live_copy


def test_render_next_game_opening_day(mlb):
    out = ANSI.sub("", sched.render_next_game("NYM"))
    assert "No games scheduled" not in out
    assert "NYM" in out and "MIA" in out and "TBD" in out
    assert "Opening Day 2027 · Thursday, March 25 · loanDepot park (at MIA) · in 167 days" in out


@pytest.mark.parametrize("path", ["/api/next/NYM", "/api/NYM/next"])
def test_api_next_offseason_shape(mlb, path):
    with TestClient(server.app) as c:
        data = c.get(path).json()
    g = data["game"]
    assert g["official_date"] == "2027-03-25" and g["away"] == "NYM" and g["home"] == "MIA"
    assert g["opening_day"] is True and g["days_until"] == 167
    assert g["countdown"] is None                      # TBD start: no fake countdown
    assert data["season_opener"]["official_date"] == "2027-03-25"


def test_api_next_in_season_has_no_opener(mlb):
    mlb["window"] = [_game(1, "2026-10-11", gtype="L", away=119, home=158, tbd=False)]
    with TestClient(server.app) as c:
        data = c.get("/api/next/LAD").json()
    assert data["game"]["opening_day"] is False and data["game"]["days_until"] == 2
    assert data["season_opener"] is None


def test_api_next_nothing_scheduled(mlb):
    mlb["openers"] = {}
    with TestClient(server.app) as c:
        data = c.get("/api/next/NYM").json()
    assert data == {"team": "NYM", "date": "2026-10-09", "game": None, "season_opener": None}


def test_standings_json_carries_its_season(monkeypatch):
    payload = {"records": [{
        "league": {"id": 104}, "division": {"name": "National League East"},
        "teamRecords": [{"team": {"id": 121, "name": "New York Mets"}, "season": "2026",
                         "divisionRank": "5", "wins": 74, "losses": 88}],
    }]}
    monkeypatch.setattr(sched, "fetch_standings", lambda: payload)
    data = sched.build_standings_json()
    assert data["season"] == 2026
    assert data["divisions"][0]["teams"][0]["rank"] == 5
