"""Offline tests for postseason.py — the bracket summary, the text render, and
/api/postseason. Payloads mirror MLB's schedule/postseason/series shape as it
looked during the 2026 Division Series: decided series drop their unplayed
games, live series keep their if-necessary ones, and unfilled slots carry
placeholder teams ("CLE/CWS", "AL Champion") with ids outside the 30 clubs."""

import os
import re
import tempfile

from datetime import date

import pytest
import requests

os.environ.setdefault("MLBSCHED_DATA_DIR", tempfile.mkdtemp(prefix="mlbsched-test-"))

from fastapi.testclient import TestClient   # noqa: E402

import postseason                            # noqa: E402
import server                                # noqa: E402

ANSI = re.compile(r"\x1b\[[0-9;]*m")
CURL = {"User-Agent": "curl/8.4.0"}

TB, NYY = (139, "Tampa Bay Rays"), (147, "New York Yankees")
CLE, CWS = (114, "Cleveland Guardians"), (145, "Chicago White Sox")
MIL, LAD = (158, "Milwaukee Brewers"), (119, "Los Angeles Dodgers")
SD, CHC = (135, "San Diego Padres"), (112, "Chicago Cubs")
KC, NYM = (118, "Kansas City Royals"), (121, "New York Mets")
CLE_CWS = (5521, "CLE/CWS")
AL_CHAMP, NL_CHAMP = (21, "AL Champion"), (31, "NL Champion")


def plain(s: str) -> str:
    return ANSI.sub("", s)


def _game(n, away, home, score=None, *, game_type="D", series="AL Division Series",
          desc="ALDS 'A'", best_of=5, gd="2026-10-03T22:30:00Z", state=None,
          tbd=False, if_necessary=False, linescore=None):
    """One postseason game. `score` is (away, home) for a Final or Live game;
    None means not started. isWinner is set the way MLB sets it on Finals."""
    state = state or ("Final" if score else "Preview")
    away_side = {"team": {"id": away[0], "name": away[1]}}
    home_side = {"team": {"id": home[0], "name": home[1]}}
    if score:
        away_side["score"], home_side["score"] = score
        if state == "Final":
            away_side["isWinner"] = score[0] > score[1]
            home_side["isWinner"] = score[1] > score[0]
    g = {
        "gamePk": 800000 + n,
        "gameType": game_type,
        "gameDate": gd,
        "officialDate": gd[:10],
        "status": {"abstractGameState": state,
                   "detailedState": {"Preview": "Scheduled"}.get(state, state),
                   "startTimeTBD": tbd},
        "seriesDescription": series,
        "description": f"{desc} Game {n}",
        "gamesInSeries": best_of,
        "seriesGameNumber": n,
        "ifNecessary": "Y" if if_necessary else "N",
        "teams": {"away": away_side, "home": home_side},
    }
    if linescore:
        g["linescore"] = linescore
    return g


def _series(*games):
    return {"series": {"id": "x", "gameType": games[0]["gameType"]}, "games": list(games)}


# ── Series fixtures ───────────────────────────────────────────────────────────

def alds_a_sweep():
    """TB (home in G1 = higher seed) sweeps NYY 3-0; no unplayed games left."""
    kw = dict(desc="ALDS 'A'")
    return _series(
        _game(1, NYY, TB, (0, 1), **kw),
        _game(2, NYY, TB, (2, 5), **kw),
        _game(3, TB, NYY, (4, 3), **kw),
    )


def alds_b_tied():
    """CLE (G1 home) and CWS split four; G5 still to play."""
    kw = dict(desc="ALDS 'B'")
    return _series(
        _game(1, CWS, CLE, (3, 0), **kw),
        _game(2, CWS, CLE, (4, 3), **kw),
        _game(3, CLE, CWS, (9, 3), **kw),
        _game(4, CLE, CWS, (9, 5), **kw),
        _game(5, CWS, CLE, gd="2026-10-11T00:08:00Z", **kw),   # Sat 10/10, 8:08 PM ET
    )


def nlds_live():
    """MIL leads 1-0, G2 in the top of the 5th, G3+ if-necessary still listed."""
    kw = dict(series="NL Division Series", desc="NLDS 'A'")
    return _series(
        _game(1, SD, MIL, (2, 3), **kw),
        _game(2, SD, MIL, (1, 2), state="Live",
              linescore={"currentInning": 5, "inningHalf": "Top"}, **kw),
        _game(3, MIL, SD, **kw),
        _game(4, MIL, SD, if_necessary=True, **kw),
        _game(5, SD, MIL, if_necessary=True, **kw),
    )


def nlwc_b_done():
    kw = dict(game_type="F", series="NL Wild Card Series", desc="NL Wild Card 'B'", best_of=3)
    return _series(_game(1, CHC, SD, (0, 8), **kw), _game(2, CHC, SD, (1, 4), **kw))


def alcs_placeholder():
    """TB awaits the ALDS 'B' winner — MLB's placeholder slot is 'CLE/CWS'."""
    kw = dict(game_type="L", series="AL Championship Series", desc="ALCS", best_of=7)
    return _series(*[_game(n, CLE_CWS, TB, gd="2026-10-13T00:08:00Z", **kw) for n in (1, 2)])


def ws_unset():
    """Both slots unfilled and first pitch not set: MLB's TBD sentinel time."""
    kw = dict(game_type="W", series="World Series", desc="World Series", best_of=7, tbd=True)
    return _series(*[_game(n, AL_CHAMP, NL_CHAMP, gd="2026-10-24T07:33:00Z", **kw) for n in (1, 2)])


def ws_decided():
    kw = dict(game_type="W", series="World Series", desc="World Series", best_of=7,
              gd="2015-10-28T00:07:00Z")
    return _series(
        _game(1, NYM, KC, (4, 5), **kw),
        _game(2, NYM, KC, (1, 7), **kw),
        _game(3, KC, NYM, (3, 9), **kw),
        _game(4, KC, NYM, (5, 3), **kw),
        _game(5, KC, NYM, (7, 2), **kw),
    )


def payload_2026():
    # Deliberately shuffled — MLB doesn't promise an order and get_bracket sorts.
    return {"series": [ws_unset(), alds_b_tied(), alcs_placeholder(), nlwc_b_done(),
                       alds_a_sweep(), nlds_live()]}


@pytest.fixture
def mlb_2026(monkeypatch):
    monkeypatch.setattr(postseason, "fetch_postseason", lambda season: payload_2026())


# ── summarize_series ──────────────────────────────────────────────────────────

def test_summarize_decided_series():
    s = postseason.summarize_series(alds_a_sweep())
    assert s["round"] == "Division Series" and s["league"] == "AL" and s["slot"] == "A"
    assert s["best_of"] == 5 and s["needed"] == 3
    assert s["top"]["id"] == TB[0] and s["bottom"]["id"] == NYY[0]   # G1 host = higher seed
    assert (s["top_wins"], s["bottom_wins"]) == (3, 0)
    assert s["winner"] is s["top"]
    assert s["live"] is None and s["next"] is None


def test_summarize_counts_finals_not_listed_games():
    s = postseason.summarize_series(alds_b_tied())
    assert (s["top_wins"], s["bottom_wins"]) == (2, 2)
    assert s["winner"] is None
    assert len(s["played"]) == 4 and s["next"]["seriesGameNumber"] == 5


def test_summarize_live_series_ignores_if_necessary_games():
    s = postseason.summarize_series(nlds_live())
    assert (s["top_wins"], s["bottom_wins"]) == (1, 0)
    assert s["winner"] is None
    assert s["live"]["seriesGameNumber"] == 2
    assert s["next"]["seriesGameNumber"] == 3


def test_summarize_skips_non_postseason_and_empty():
    assert postseason.summarize_series({"games": []}) is None
    assert postseason.summarize_series(_series(_game(1, NYM, KC, game_type="R"))) is None
    assert postseason.summarize_series(_series(_game(1, NYM, KC, game_type="S"))) is None


def test_get_bracket_orders_rounds_leagues_and_slots(mlb_2026):
    order = [(s["game_type"], s["league"], s["slot"]) for s in postseason.get_bracket(2026)]
    assert order == [("F", "NL", "B"), ("D", "AL", "A"), ("D", "AL", "B"),
                     ("D", "NL", "A"), ("L", "AL", ""), ("W", "", "")]


# ── render_postseason ─────────────────────────────────────────────────────────

def _line(out: str, tag: str) -> str:
    return next(l for l in out.splitlines() if l.strip().startswith(tag))


def test_render_bracket(mlb_2026):
    out = plain(postseason.render_postseason(2026))
    assert "MLB Postseason — 2026" in out
    assert "Wild Card Series  (best of 3)" in out
    assert "Division Series  (best of 5)" in out
    assert "League Championship Series  (best of 7)" in out
    assert "World Series  (best of 7)" in out

    assert "TB win 3-0" in _line(out, "ALDS A")
    assert "SD win 2-0" in _line(out, "NLWC B")
    # Undecided: series record, then the next game in ET (UTC date is a day later).
    b = _line(out, "ALDS B")
    assert re.search(r"CLE\s+2 -\s+CWS\s+2", b) and "G5 Sat 10/10 8:08 PM EDT" in b
    # Live: inning arrow and the in-progress score.
    assert "● G2 ▲5" in _line(out, "NLDS A") and re.search(r"SD\s+1\s+MIL\s+2", _line(out, "NLDS A"))
    assert "🏆" not in out


def test_render_placeholder_slots_and_tbd_time(mlb_2026):
    out = plain(postseason.render_postseason(2026))
    alcs = _line(out, "ALCS")
    assert re.search(r"TB\s+vs TBD", alcs)
    assert "CLE/CWS @ Tampa Bay Rays" in out            # what the TBD slot is waiting on
    ws = _line(out, "WS")
    assert re.search(r"TBD\s+vs TBD", ws)
    assert "G1 Sat 10/24 TBD" in ws and "3:33" not in ws  # 07:33Z is MLB's "no time yet" sentinel
    assert "AL Champion @ NL Champion" in out


def test_render_series_columns_align(mlb_2026):
    """Started and not-started series must put the status column in the same place."""
    starts = set()
    for s in postseason.get_bracket(2026):
        line = plain(postseason._series_line(s, None))
        starts.add(line.index(plain(postseason._status_text(s, None))))
    assert len(starts) == 1, starts


def test_render_world_series_champion(monkeypatch):
    monkeypatch.setattr(postseason, "fetch_postseason", lambda season: {"series": [ws_decided()]})
    out = plain(postseason.render_postseason(2015))
    assert "KC win 4-1" in out
    assert "🏆 2015 World Series champions: Kansas City Royals" in out


def test_render_empty_and_unreachable(monkeypatch):
    monkeypatch.setattr(postseason, "today_et", lambda: date(2026, 10, 9))
    monkeypatch.setattr(postseason, "fetch_postseason", lambda season: {"series": []})
    assert "No postseason schedule published for 2026 yet." in plain(postseason.render_postseason(2026))
    assert "No postseason was played in 1994." in plain(postseason.render_postseason(1994))

    # The renderer no longer swallows an outage; the server maps it to a 503.
    def down(season):
        raise requests.ConnectionError("down")
    monkeypatch.setattr(postseason, "fetch_postseason", down)
    with pytest.raises(requests.ConnectionError):
        postseason.render_postseason(2026)


def _published_through(last_year):
    """fetch_postseason with a decided bracket for every season up to last_year."""
    return lambda season: {"series": [ws_decided()]} if season <= last_year else {"series": []}


def test_bare_postseason_falls_back_to_last_season(monkeypatch):
    monkeypatch.setattr(postseason, "today_et", lambda: date(2027, 1, 15))
    monkeypatch.setattr(postseason, "fetch_postseason", _published_through(2026))
    assert postseason.default_season() == (2026, True)
    out = plain(postseason.render_postseason())
    assert "MLB Postseason — 2026" in out
    assert "The 2027 bracket isn't set yet — showing 2026." in out
    assert postseason.build_postseason_json()["season"] == 2026


def test_bare_postseason_uses_this_season_once_published(monkeypatch):
    monkeypatch.setattr(postseason, "today_et", lambda: date(2027, 10, 1))
    monkeypatch.setattr(postseason, "fetch_postseason", _published_through(2027))
    assert postseason.default_season() == (2027, False)
    out = plain(postseason.render_postseason())
    assert "MLB Postseason — 2027" in out and "isn't set yet" not in out


# ── fetch_postseason cache ────────────────────────────────────────────────────

class _Resp:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self._data


def test_fetch_caches_and_serves_stale(monkeypatch):
    monkeypatch.setattr(postseason, "_cache", {})
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(params["season"])
        return _Resp({"series": ["ok"]})
    monkeypatch.setattr(postseason.requests, "get", fake_get)
    assert postseason.fetch_postseason(2026) == {"series": ["ok"]}
    assert postseason.fetch_postseason(2026) == {"series": ["ok"]}
    assert calls == [2026]                                   # second call inside the TTL

    # Past the TTL and MLB is down: the last good bracket, not an exception.
    fetched_at, data = postseason._cache[2026]
    postseason._cache[2026] = (fetched_at - postseason._TTL_SECONDS - 1, data)

    def down(url, params=None, timeout=None):
        raise requests.ConnectionError("down")
    monkeypatch.setattr(postseason.requests, "get", down)
    assert postseason.fetch_postseason(2026) == {"series": ["ok"]}
    with pytest.raises(requests.ConnectionError):
        postseason.fetch_postseason(2025)                    # nothing cached to fall back on


# ── HTTP: /postseason and /api/postseason ─────────────────────────────────────

@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(server, "geolocate_ip", lambda ip: None)   # no ip-api calls
    with TestClient(server.app) as c:
        yield c


def test_api_postseason_shape(client, mlb_2026):
    r = client.get("/api/postseason/2026")
    assert r.status_code == 200
    data = r.json()
    assert data["season"] == 2026 and data["champion"] is None
    by_tag = {(s["game_type"], s["league"], s["slot"]): s for s in data["series"]}

    sweep = by_tag[("D", "AL", "A")]
    assert sweep["status"] == "final" and sweep["winner"] == "TB"
    assert sweep["top"] == {"team": "TB", "name": "Tampa Bay Rays", "placeholder": False}
    assert (sweep["top_wins"], sweep["bottom_wins"]) == (3, 0)
    assert [g["winner"] for g in sweep["games"]] == ["TB", "TB", "TB"]

    assert by_tag[("D", "AL", "B")]["status"] == "in_progress"
    live = by_tag[("D", "NL", "A")]
    assert live["status"] == "live"
    assert [g["if_necessary"] for g in live["games"]] == [False, False, False, True, True]
    assert live["games"][1]["winner"] is None                # a Live game has no winner yet

    alcs = by_tag[("L", "AL", None)]
    assert alcs["status"] == "scheduled" and alcs["league"] == "AL" and alcs["slot"] is None
    assert alcs["bottom"] == {"team": None, "name": "CLE/CWS", "placeholder": True}

    ws = by_tag[("W", None, None)]
    assert ws["games"][0]["start_time_tbd"] is True
    assert ws["top"]["placeholder"] and ws["bottom"]["placeholder"]


def test_api_postseason_champion(client, monkeypatch):
    monkeypatch.setattr(postseason, "fetch_postseason", lambda season: {"series": [ws_decided()]})
    assert client.get("/api/postseason/2015").json()["champion"] == "KC"


def test_api_postseason_upstream_down_is_503(client, monkeypatch):
    def down(season):
        raise requests.ConnectionError("down")
    monkeypatch.setattr(postseason, "fetch_postseason", down)
    r = client.get("/api/postseason/2026")
    assert r.status_code == 503 and "error" in r.json()


def test_postseason_text_routes(client, mlb_2026):
    for path in ("/postseason/2026", "/playoffs", "/bracket"):
        r = client.get(path, headers=CURL)
        assert r.status_code == 200 and "MLB Postseason" in plain(r.text), path


@pytest.mark.parametrize("season", ["1902", "2999", "abc"])
def test_postseason_unknown_season_is_404(client, season):
    r = client.get(f"/postseason/{season}", headers=CURL)
    assert r.status_code == 404 and "Unknown season" in plain(r.text)


def test_postseason_text_upstream_down_is_503(client, monkeypatch):
    def down(season):
        raise requests.ConnectionError("down")
    monkeypatch.setattr(postseason, "fetch_postseason", down)
    for path in ("/postseason/2026", "/postseason"):
        r = client.get(path, headers=CURL)
        assert r.status_code == 503 and r.headers["retry-after"] == "30", path
    assert client.get("/api/postseason", headers=CURL).status_code == 503


@pytest.mark.parametrize("season", [1902, 2999])
def test_api_postseason_unknown_season_is_404(client, season):
    r = client.get(f"/api/postseason/{season}")
    assert r.status_code == 404 and r.json() == {"error": f"Unknown season: {season}"}


def test_api_postseason_season_without_a_postseason_is_empty(client, monkeypatch):
    monkeypatch.setattr(postseason, "fetch_postseason", lambda season: {"series": []})
    r = client.get("/api/postseason/1994")
    assert r.status_code == 200 and r.json()["series"] == []
