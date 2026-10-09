"""/watch and /replay: the live-game screen. Feeds are small synthetic payloads in
the MLB live-feed shape, so this runs offline like the rest of the suite."""

import os
import re
import tempfile
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("MLBSCHED_DATA_DIR", tempfile.mkdtemp(prefix="mlbsched-test-"))

from fastapi.testclient import TestClient   # noqa: E402

import server                                # noqa: E402
import watch                                 # noqa: E402

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
CURL = {"User-Agent": "curl/8.4.0"}
BROWSER = {"User-Agent": "Mozilla/5.0"}

CLE, CWS = 114, 145
PITCHER = {"id": 1, "fullName": "Gavin Williams"}
RELIEVER = {"id": 2, "fullName": "Tim Herrin"}


def pitch(desc="Ball", kind="Slider", mph=85.5):
    return {"isPitch": True, "details": {"description": desc, "type": {"description": kind}},
            "pitchData": {"startSpeed": mph}}


def play(batter, pitcher, events, text=None, inning=5, half="bottom", scoring=False):
    return {
        "result": {"description": text, "event": "Single" if text else None},
        "about": {"inning": inning, "halfInning": half, "isComplete": text is not None,
                  "isScoringPlay": scoring},
        "matchup": {"batter": {"id": batter["id"], "fullName": batter["fullName"]},
                    "pitcher": {"id": pitcher["id"], "fullName": pitcher["fullName"]},
                    "batSide": {"code": "L"}, "pitchHand": {"code": "R"}},
        "playEvents": events,
    }


def feed(state="Live", detailed="In Progress", inning_state="Bottom", plays=None,
         innings=None, runs=(3, 4), start="2026-10-11T00:00:00Z", decisions=None):
    kwan = {"id": 10, "fullName": "Steven Kwan"}
    teel = {"id": 11, "fullName": "Kyle Teel"}
    return {
        "metaData": {"timeStamp": "20261009_013000"},
        "gameData": {
            "game": {"pk": 849832, "type": "D"},
            "status": {"abstractGameState": state, "detailedState": detailed, "startTimeTBD": False},
            "teams": {"away": {"id": CLE, "name": "Cleveland Guardians"},
                      "home": {"id": CWS, "name": "Chicago White Sox"}},
            "datetime": {"dateTime": start, "officialDate": "2026-10-10"},
            "venue": {"name": "Rate Field"},
            "probablePitchers": {"away": PITCHER, "home": {"id": 3, "fullName": "Hagen Smith"}},
        },
        "liveData": {
            "linescore": {
                "currentInning": 5, "currentInningOrdinal": "5th", "inningState": inning_state,
                "scheduledInnings": 9,
                "innings": innings if innings is not None else [
                    {"num": i, "away": {"runs": 0}, "home": {"runs": 1 if i == 3 else 0}} for i in range(1, 5)
                ] + [{"num": 5, "away": {"runs": 3}, "home": {"runs": 3}}],
                "teams": {"away": {"runs": runs[0], "hits": 6, "errors": 0},
                          "home": {"runs": runs[1], "hits": 7, "errors": 1}},
                "offense": {"batter": teel, "first": kwan, "third": {"id": 12, "fullName": "Jo Adell"}},
                "defense": {"pitcher": PITCHER},
                "balls": 2, "strikes": 1, "outs": 1,
            },
            "plays": {"allPlays": plays if plays is not None else [
                play(kwan, PITCHER, [pitch(), pitch("Foul")], text="Steven Kwan singles to right."),
                play(teel, PITCHER, [pitch(), pitch("Ball"), pitch("Called Strike", "Four-Seam Fastball", 97.8)]),
            ]},
            "decisions": decisions or {},
        },
    }


GAME = {"gamePk": 849832, "gameType": "D", "seriesDescription": "AL Division Series",
        "seriesGameNumber": 4, "gameDate": "2026-10-09T00:00:00Z",
        "status": {"abstractGameState": "Live", "detailedState": "In Progress"},
        "teams": {"away": {"team": {"id": CLE}}, "home": {"team": {"id": CWS}}}}


def plain(s: str) -> str:
    return ANSI.sub("", s)


# ── snapshot ──────────────────────────────────────────────────────────────────
def test_snapshot_reads_the_live_state():
    s = watch.snapshot(feed(), GAME)
    assert s["status"] == "Live" and s["tag"] == "ALDS G4"
    assert (s["away"]["abv"], s["home"]["abv"]) == ("CLE", "CWS")
    assert s["runners"] == {"first": "Steven Kwan", "second": None, "third": "Jo Adell"}
    assert (s["balls"], s["strikes"], s["outs"]) == (2, 1, 1)
    assert s["batter"] == {"name": "Kyle Teel", "bats": "L"}
    assert s["pitcher"]["pitches"] == 5            # every pitch thrown by this pitcher, across plays
    assert s["last_pitch"] == {"call": "Called Strike", "type": "Four-Seam Fastball", "mph": 97.8}
    assert s["last_play"]["text"] == "Steven Kwan singles to right."


def test_last_pitch_belongs_to_the_current_batter_only():
    teel = {"id": 11, "fullName": "Kyle Teel"}
    plays = [play(teel, PITCHER, [pitch("Ball In Dirt")], text="Kyle Teel walks."),
             play({"id": 13, "fullName": "Braden Montgomery"}, PITCHER, [])]
    assert watch.snapshot(feed(plays=plays), GAME)["last_pitch"] is None


def test_non_pitch_event_becomes_a_note():
    teel = {"id": 11, "fullName": "Kyle Teel"}
    plays = [play(teel, PITCHER, [pitch(), {"isPitch": False, "details": {"description": "Mound Visit."}}])]
    assert watch.snapshot(feed(plays=plays), GAME)["note"] == "Mound Visit."


# ── rendering ─────────────────────────────────────────────────────────────────
def test_live_frame():
    out = watch.render_frame(watch.snapshot(feed(), GAME), mode="stream")
    text = plain(out)
    assert "▼ 5th" in text and "ALDS G4" in text
    assert "AB  Kyle Teel (L)" in text
    assert "Gavin Williams (R)  · 5 pitches" in text
    assert "97.8 mph Four-Seam Fastball — Called Strike" in text
    assert "» Steven Kwan singles to right." in text
    assert "ctrl-c to quit" in text
    assert text.count("◆") == 2                       # first and third occupied
    assert "\x1b[H" not in out and "\x1b[K" not in out   # cursor control belongs to paint()


def test_last_play_from_the_previous_half_is_labeled():
    kwan = {"id": 10, "fullName": "Steven Kwan"}
    plays = [play(kwan, PITCHER, [pitch()], text="Steven Kwan grounds out.", inning=5, half="top")]
    text = plain(watch.render_frame(watch.snapshot(feed(plays=plays), GAME)))
    assert "Top 5: Steven Kwan grounds out." in text


def test_between_halves_hides_count_and_matchup():
    kwan = {"id": 10, "fullName": "Steven Kwan"}
    plays = [play(kwan, PITCHER, [pitch()], text="Steven Kwan grounds out.", inning=5, half="top")]
    text = plain(watch.render_frame(watch.snapshot(feed(inning_state="Middle", plays=plays), GAME)))
    assert "Middle 5th" in text
    assert "AB " not in text and "B ●" not in text
    assert "» Steven Kwan grounds out." in text          # the half that just ended needs no label
    assert "\n\n\n" not in text


def test_final_frame():
    innings = [{"num": i, "away": {"runs": 0}, "home": {"runs": 0}} for i in range(1, 9)]
    innings.append({"num": 9, "away": {"runs": 0}, "home": {}})   # home never batted in the 9th
    f = feed(state="Final", detailed="Final", innings=innings, runs=(2, 3),
             decisions={"winner": {"fullName": "Hagen Smith"}, "loser": {"fullName": "Gavin Williams"}})
    s = watch.snapshot(f, GAME)
    assert watch.is_over(s) and not watch.worth_streaming(s)
    text = plain(watch.render_frame(s))
    assert "Final" in text and "W Hagen Smith" in text and "L Gavin Williams" in text
    assert re.search(r"CWS\s+(0\s+){8}x", text)          # the unneeded bottom 9th reads x
    assert "curl mlbsched.run/box/CWS/2026-10-10" in text


def test_pregame_frame_and_streaming_window():
    soon = (datetime.now(timezone.utc) + timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    later = (datetime.now(timezone.utc) + timedelta(hours=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    s = watch.snapshot(feed(state="Preview", detailed="Pre-Game", start=soon), GAME)
    text = plain(watch.render_frame(s))
    assert "First pitch" in text and "Probable starters" in text and "Gavin Williams" in text
    assert watch.worth_streaming(s)
    assert not watch.worth_streaming(watch.snapshot(feed(state="Preview", start=later), GAME))
    assert watch.worth_streaming(watch.snapshot(feed(), GAME))


def test_paint_redraws_in_place():
    out = watch.paint("a\nb\n")
    assert out == "\x1b[Ha\x1b[K\nb\x1b[K\n\x1b[J"


# ── routes ────────────────────────────────────────────────────────────────────
@pytest.fixture
def client(monkeypatch):
    watch._feed_cache.clear()
    monkeypatch.setattr(server, "geolocate_ip", lambda ip: None)
    monkeypatch.setattr(watch, "find_game", lambda abv: GAME)
    monkeypatch.setattr(watch, "_INTERVAL", {"Live": 0, "Preview": 0})
    monkeypatch.setattr(watch, "_FEED_TTL", {"Live": 0, "Preview": 0, "Final": 0})
    with TestClient(server.app) as c:
        yield c


def serve(monkeypatch, *feeds):
    """Each upstream fetch returns the next feed; the last one repeats."""
    seq = list(feeds)
    monkeypatch.setattr(watch, "_get_feed", lambda pk, tc=None: seq.pop(0) if len(seq) > 1 else seq[0])


def test_watch_unknown_team_is_404(client):
    assert client.get("/watch/ZZZ", headers=CURL).status_code == 404
    assert client.get("/api/watch/ZZZ").status_code == 404


def test_watch_streams_until_final(client, monkeypatch):
    final = feed(state="Final", detailed="Final", runs=(2, 3))
    serve(monkeypatch, feed(), feed(), feed(), final)   # the route's own check takes the first
    r = client.get("/watch/CLE", headers=CURL)
    assert r.status_code == 200
    assert r.headers["cache-control"] == "no-store, no-transform"
    assert r.text.startswith(watch.CLEAR)
    frames = r.text.split("\x1b[H")[2:]                 # [0] clear, [1] CLEAR's own home
    assert len(frames) == 3
    assert "▼ 5th" in plain(frames[0]) and "Final" in plain(frames[-1])
    assert watch.active_streams() == 0


def test_watch_once_is_a_single_snapshot(client, monkeypatch):
    serve(monkeypatch, feed())
    r = client.get("/watch/CLE?once=1", headers=CURL)
    assert r.status_code == 200 and "\x1b[H" not in r.text
    assert "▼ 5th" in plain(r.text) and "ctrl-c" not in r.text


def test_watch_far_off_game_explains_when_it_opens(client, monkeypatch):
    later = (datetime.now(timezone.utc) + timedelta(hours=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    serve(monkeypatch, feed(state="Preview", detailed="Scheduled", start=later))
    text = plain(client.get("/watch/CLE", headers=CURL).text)
    assert "First pitch" in text and "opens 3 hours before first pitch" in text


def test_watch_browser_gets_a_refreshing_page(client, monkeypatch):
    serve(monkeypatch, feed())
    r = client.get("/watch/CLE", headers=BROWSER)
    assert r.headers["content-type"].startswith("text/html")
    assert 'http-equiv="refresh" content="10"' in r.text and "\x1b[" not in r.text


def test_api_watch(client, monkeypatch):
    serve(monkeypatch, feed())
    body = client.get("/api/watch/CLE").json()
    assert body["team"] == "CLE"
    assert body["game"]["runners"]["first"] == "Steven Kwan"
    assert body["game"]["pitcher"]["pitches"] == 5


def test_replay_streams_a_finished_game(client, monkeypatch):
    final_game = {**GAME, "status": {"abstractGameState": "Final", "detailedState": "Final"}}
    monkeypatch.setattr(watch, "find_game_by_pk", lambda pk: final_game)
    monkeypatch.setattr(watch, "fetch_timestamps", lambda pk: ["t0", "t1", "t2"])
    monkeypatch.setattr(watch, "REPLAY_STEP_SECONDS", 0)
    frames = {"t0": feed(state="Preview"), "t1": feed(), "t2": feed(state="Final", detailed="Final")}
    monkeypatch.setattr(watch, "_get_feed", lambda pk, tc=None: frames[tc])
    r = client.get("/replay/849832?speed=10", headers=CURL)
    shown = r.text.split("\x1b[H")[2:]
    assert len(shown) == 2                                # pre-game timecodes are skipped
    assert "REPLAY" in plain(shown[0]) and "Final" in plain(shown[-1])


def test_replay_refuses_a_game_in_progress(client, monkeypatch):
    monkeypatch.setattr(watch, "find_game_by_pk", lambda pk: GAME)
    r = client.get("/replay/849832", headers=CURL)
    assert r.status_code == 400 and "watch it live" in plain(r.text)
