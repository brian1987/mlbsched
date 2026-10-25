"""/winprob: the chart of how a finished game's odds swung, still or drawn play by
play. Plays are small synthetic payloads in the shape of MLB's winProbability feed."""

import os
import re
import tempfile

import pytest

os.environ.setdefault("MLBSCHED_DATA_DIR", tempfile.mkdtemp(prefix="mlbsched-test-"))

from fastapi.testclient import TestClient   # noqa: E402

import server                                # noqa: E402
import watch                                 # noqa: E402
import wp                                    # noqa: E402

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
CURL = {"User-Agent": "curl/8.4.0"}
BROWSER = {"User-Agent": "Mozilla/5.0"}
CLE, CWS = 114, 145


def plain(s: str) -> str:
    return ANSI.sub("", s)


def wp_play(home_wp, added, inning, top, away_runs, home_runs, desc):
    return {"homeTeamWinProbability": home_wp, "awayTeamWinProbability": 100 - home_wp,
            "homeTeamWinProbabilityAdded": added,
            "about": {"inning": inning, "isTopInning": top, "halfInning": "top" if top else "bottom"},
            "result": {"awayScore": away_runs, "homeScore": home_runs, "description": desc}}


# CWS (away) scores first, CLE (home) ties it, then walks it off: one lead change.
PLAYS = [
    wp_play(30.0, -20.0, 1, True, 1, 0, "Lenyn Sosa homers (1) to left field."),
    wp_play(32.0, 2.0, 1, False, 1, 0, "Steven Kwan grounds out."),
    wp_play(28.0, -4.0, 2, True, 1, 0, "Chase Meidroth singles."),
    wp_play(50.0, 22.0, 2, False, 1, 1, "Jose Ramirez doubles, scoring Kwan."),
    wp_play(45.0, -5.0, 3, True, 1, 1, "Andrew Benintendi walks."),
    wp_play(100.0, 55.0, 3, False, 1, 2, "Kyle Manzardo singles, scoring Ramirez."),
]
GAME = {"gamePk": 849831, "gameType": "D", "seriesDescription": "AL Division Series",
        "seriesGameNumber": 5, "gameDate": "2026-10-11T00:08:00Z", "officialDate": "2026-10-10",
        "status": {"abstractGameState": "Final", "detailedState": "Final"},
        "teams": {"away": {"team": {"id": CWS, "name": "Chicago White Sox"}, "score": 1},
                  "home": {"team": {"id": CLE, "name": "Cleveland Guardians"}, "score": 2}}}


# ── chart ─────────────────────────────────────────────────────────────────────
def test_top_swings_are_the_biggest_moves_in_game_order():
    assert wp.top_swings(PLAYS) == [0, 3, 5]
    assert wp.top_swings(PLAYS[:2], k=3) == [0, 1]


def test_chart_stacks_home_up_and_away_down():
    lines = [plain(ln) for ln in wp.render_chart(PLAYS, "CLE", "CWS", marks=[0, 3, 5])]
    rows, side = lines[:wp.CHART_ROWS], wp.CHART_ROWS // 2
    assert rows[0].lstrip().startswith("CLE") and rows[-1].lstrip().startswith("CWS")
    assert "50%" in rows[side]
    chart = [r.split("┤", 1)[-1].split("│", 1)[-1].split("┼", 1)[-1] for r in rows]
    assert "█" in chart[0][-3:]                               # the walk-off: CLE at 100%
    assert any("█" in r[:10] for r in chart[side + 1:])       # CWS ahead early
    marked = "".join(chart)
    assert all(str(k) in marked for k in (1, 2, 3))
    assert lines[-1].split() == ["1", "2", "3"]               # inning ticks


def test_chart_draws_only_through_upto():
    full = wp.render_chart(PLAYS, "CLE", "CWS")
    part = wp.render_chart(PLAYS, "CLE", "CWS", upto=2, marks=[0, 3, 5])
    assert "█" not in plain(part[0])                          # the walk-off hasn't happened yet
    assert "█" in plain(full[0])
    assert "2" not in "".join(plain(ln) for ln in part[:wp.CHART_ROWS]).replace("CLE", "")


def test_game_story():
    line = plain(wp.game_story(PLAYS, "CLE", "CWS"))
    assert line == "CLE won 2–1. CLE's low point: 28% (top 2nd). 1 lead change."
    wire = [wp_play(60.0, 10.0, 1, False, 0, 1, "x"), wp_play(70.0, 10.0, 2, True, 0, 1, "y")]
    assert "CLE never trailed in the odds. 0 lead changes." in plain(wp.game_story(wire, "CLE", "CWS"))


# ── routes ────────────────────────────────────────────────────────────────────
@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(server, "geolocate_ip", lambda ip: None)
    monkeypatch.setattr(wp, "fetch_schedule", lambda d, team_id=None: {"dates": [{"games": [GAME]}]})
    monkeypatch.setattr(wp, "fetch_wp", lambda pk: PLAYS)
    monkeypatch.setattr(watch, "find_team_game", lambda abv, d, n=1: GAME)
    monkeypatch.setattr(watch, "last_final", lambda team_id=None: GAME)
    monkeypatch.setattr(wp, "PLAY_SECONDS", 0)
    monkeypatch.setattr(wp, "SWING_PAUSE", 0)
    with TestClient(server.app) as c:
        yield c


def test_winprob_page(client):
    r = client.get("/winprob/CLE/2026-10-10", headers=CURL)
    text = plain(r.text)
    assert r.status_code == 200
    assert "CLE won 2–1." in text and "Biggest swings" in text and "Kyle Manzardo singles" in text
    assert 'curl -N "mlbsched.run/winprob/CLE/2026-10-10?play=1"' in text
    assert client.get("/wp/CLE/2026-10-10", headers=CURL).text == r.text          # the old name still works


def test_winprob_without_a_date_charts_the_last_game(client, monkeypatch):
    asked = []
    monkeypatch.setattr(wp, "fetch_schedule", lambda d, team_id=None: asked.append(d) or {"dates": [{"games": [GAME]}]})
    assert "CLE won 2–1." in plain(client.get("/winprob/cle", headers=CURL).text)
    assert asked == ["2026-10-10"]
    assert client.get("/api/winprob/CLE").json()["date"] == "2026-10-10"


def test_winprob_errors(client):
    assert client.get("/winprob/ZZZ", headers=CURL).status_code == 404
    assert client.get("/winprob/CLE/someday", headers=CURL).status_code == 404


def test_winprob_waits_for_a_finished_game(client, monkeypatch):
    live = {**GAME, "status": {"abstractGameState": "Live", "detailedState": "In Progress"}}
    monkeypatch.setattr(wp, "fetch_schedule", lambda d, team_id=None: {"dates": [{"games": [live]}]})
    assert "drawn once the game is over" in plain(client.get("/winprob/CLE/2026-10-10", headers=CURL).text)


def test_play_draws_the_chart_then_shows_the_final(client):
    r = client.get("/winprob/CLE/2026-10-10?play=1", headers=CURL)
    assert r.headers["cache-control"] == "no-store, no-transform"
    assert r.text.startswith(watch.CLEAR)
    frames = [plain(f) for f in r.text.split("\x1b[H")[2:]]
    assert len(frames) == len(PLAYS) + 1
    assert "top 1st  CWS 1, CLE 0" in frames[0] and "Lenyn Sosa homers" in frames[0]
    assert all("Final" not in f and "won" not in f for f in frames[:-1])           # no spoilers
    assert "Final" in frames[-1] and "CLE won 2–1." in frames[-1] and "Biggest swings" in frames[-1]


def test_play_in_a_browser_is_the_still_chart(client):
    r = client.get("/winprob/CLE/2026-10-10?play=1", headers=BROWSER)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert "Biggest swings" in r.text
