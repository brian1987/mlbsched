"""/watch and /replay: the live-game screen. Feeds are small synthetic payloads in
the MLB live-feed shape, so this runs offline like the rest of the suite."""

import os
import re
import tempfile
from datetime import date, datetime, timedelta, timezone

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


def odds_frame(odds, **kw):
    return watch.render_frame(watch.snapshot(feed(**kw), GAME), mode="stream", odds=odds)


def odds_rows(odds, **kw):
    """The two rows beside home plate, as plain text."""
    lines = plain(odds_frame(odds, **kw)).split("\n")
    at = next(i for i, l in enumerate(lines) if "⌂" in l)
    return lines[at - 1].strip(), lines[at].strip()


def test_odds_sit_beside_home_plate_without_moving_anything():
    bare, with_odds = odds_frame(None), odds_frame([55.0, 64.2])
    a, b = bare.split("\n"), with_odds.split("\n")
    assert len(a) == len(b)                                # same height: nothing pushed down
    changed = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
    assert len(changed) == 2 and changed[1] == changed[0] + 1
    assert all(b[i].startswith(a[i]) for i in changed)     # the diamond itself is untouched
    upper, lower = (plain(b[i]) for i in changed)
    assert upper.endswith("▄  CWS 64%") and lower.endswith("to win")   # home's odds above the line
    assert max(len(plain(l)) for l in b) <= watch.WIDTH + 2


def test_odds_name_the_favorite():
    upper, lower = odds_rows([40.0, 29.6])
    assert upper.endswith("CLE 70%") and lower.endswith("▀  to win")   # away's below the line
    assert odds_rows([50.2])[0].endswith("Even") and odds_rows([50.2])[1].endswith("odds")
    assert odds_rows([99.8])[0].endswith("█  CWS 99%")    # not over until the last out


def test_odds_chart_holds_still_as_the_game_goes():
    game = [50 + (i % 10) * 4.0 - 18 for i in range(200)]

    def chart(n):
        cols = -(-n // max(watch.ODDS_PER_CELL, -(-n // watch.ODDS_WIDTH)))
        snap = watch.snapshot(feed(), GAME)
        return tuple(plain(row)[:cols] for row in watch._odds_lines(snap, game[:n]))

    assert len(chart(4)[0]) == 1 and len(chart(5)[0]) == 2
    for n in range(4, 80):
        (top, bottom), (top2, bottom2) = chart(n), chart(n + 1)
        assert top2[:len(top) - 1] == top[:-1] and bottom2[:len(bottom) - 1] == bottom[:-1]
    assert len(chart(80)[0]) == watch.ODDS_WIDTH
    assert len(chart(200)[0]) == watch.ODDS_WIDTH          # a 20-inning game still fits


def test_odds_show_between_halves_but_not_once_final():
    assert odds_rows([70.0], inning_state="Middle")[0].endswith("CWS 70%")
    assert "to win" not in plain(odds_frame([100.0], state="Final", detailed="Final"))


def test_odds_refetch_only_when_a_play_has_finished(monkeypatch):
    watch._odds_cache.clear()
    calls = []
    monkeypatch.setattr(watch, "_get_odds", lambda pk: calls.append(pk) or [52.0, 58.0, 61.0][:len(calls) + 1])
    assert watch.game_odds(1, 2) == [52.0, 58.0]
    assert watch.game_odds(1, 2) == [52.0, 58.0] and len(calls) == 1     # nothing new: no fetch
    assert watch.game_odds(1, 3) == [52.0, 58.0] and len(calls) == 1     # behind, but fetched just now
    monkeypatch.setattr(watch, "ODDS_REFRESH", 0)
    assert watch.game_odds(1, 3) == [52.0, 58.0, 61.0] and len(calls) == 2
    assert watch.game_odds(1, 1) == [52.0]                 # never ahead of the screen


def test_odds_failures_keep_the_last_good_odds(monkeypatch):
    watch._odds_cache.clear()
    monkeypatch.setattr(watch, "ODDS_REFRESH", 0)
    monkeypatch.setattr(watch, "_get_odds", lambda pk: [55.0])
    assert watch.game_odds(2, 1) == [55.0]

    def down(pk):
        raise watch.requests.ConnectionError("MLB down")
    monkeypatch.setattr(watch, "_get_odds", down)
    assert watch.game_odds(2, 2) == [55.0]
    monkeypatch.setattr(watch, "_get_odds", lambda pk: 1 / 0)          # anything at all
    assert watch.game_odds(3, 1) == []


def test_get_odds_keeps_finished_plays_in_order(monkeypatch):
    class Resp:
        def raise_for_status(self): pass
        def json(self):
            return [{"about": {"atBatIndex": 1, "isComplete": True}, "homeTeamWinProbability": 60.0},
                    {"about": {"atBatIndex": 0, "isComplete": True}, "homeTeamWinProbability": 45.5},
                    {"about": {"atBatIndex": 2, "isComplete": False}, "homeTeamWinProbability": 61.0}]
    asked = {}
    monkeypatch.setattr(watch.requests, "get", lambda url, params=None, timeout=None: asked.update(params) or Resp())
    assert watch._get_odds(5) == [45.5, 60.0]
    assert "homeTeamWinProbability" in asked["fields"]


def test_paint_redraws_in_place():
    out = watch.paint("a\nb\n")
    assert out == "\x1b[Ha\x1b[K\nb\x1b[K\n\x1b[J"


# ── routes ────────────────────────────────────────────────────────────────────
@pytest.fixture
def client(monkeypatch):
    watch._feed_cache.clear()
    watch._odds_cache.clear()
    monkeypatch.setattr(watch, "_get_odds", lambda pk: [])
    monkeypatch.setattr(server, "geolocate_ip", lambda ip: None)
    monkeypatch.setattr(watch, "find_game", lambda abv: GAME)
    monkeypatch.setattr(watch, "last_final", lambda team_id=None: None)
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


def test_watch_stream_shows_the_odds(client, monkeypatch):
    monkeypatch.setattr(watch, "_get_odds", lambda pk: [57.0, 61.0])   # one play done: 57
    serve(monkeypatch, feed(), feed(), feed(state="Final", detailed="Final", runs=(2, 3)))
    frames = [plain(f) for f in client.get("/watch/CLE", headers=CURL).text.split("\x1b[H")[2:]]
    assert "CWS 57%" in frames[0] and "to win" in frames[0] and "to win" not in frames[-1]


def test_watch_shows_the_odds_on_every_view(client, monkeypatch):
    monkeypatch.setattr(watch, "_get_odds", lambda pk: [57.0, 61.0])   # one play done: 57
    serve(monkeypatch, feed())
    assert "CWS 57%" in plain(client.get("/watch/CLE?once=1", headers=CURL).text)
    assert "57%" in client.get("/watch/CLE", headers=BROWSER).text
    assert client.get("/api/watch/CLE").json()["game"]["win_probability"] == {"home": 57.0, "away": 43.0}


def test_watch_without_odds_looks_as_it_did(client, monkeypatch):
    def down(pk):
        raise watch.requests.ConnectionError("MLB down")
    monkeypatch.setattr(watch, "_get_odds", down)
    serve(monkeypatch, feed())
    r = client.get("/watch/CLE?once=1", headers=CURL)
    assert r.status_code == 200 and "to win" not in r.text and "▼ 5th" in plain(r.text)
    assert client.get("/api/watch/CLE").json()["game"]["win_probability"] is None


def test_watch_points_a_finished_game_at_its_chart(client, monkeypatch):
    monkeypatch.setattr(watch, "find_game", lambda abv: {**GAME, "officialDate": "2026-10-10"})
    serve(monkeypatch, feed(state="Final", detailed="Final", runs=(2, 3)))
    assert "See how the odds swung: curl mlbsched.run/winprob/CLE/2026-10-10" in plain(
        client.get("/watch/CLE", headers=CURL).text)
    assert "mlbsched.run/winprob/CLE/2026-10-10" in plain(client.get("/watch/CLE", headers=BROWSER).text)
    assert "winprob" not in client.get("/watch/CLE?once=1", headers=CURL).text   # scripts get the frame alone
    serve(monkeypatch, feed(state="Final", detailed="Postponed"))
    assert "winprob" not in client.get("/watch/CLE", headers=CURL).text          # nothing to chart


def test_watch_stream_ends_with_the_chart_pointer(client, monkeypatch):
    monkeypatch.setattr(watch, "find_game", lambda abv: {**GAME, "officialDate": "2026-10-10"})
    serve(monkeypatch, feed(), feed(), feed(state="Final", detailed="Final", runs=(2, 3)))
    tail = plain(client.get("/watch/cle", headers=CURL).text.split("\x1b[H")[-1])
    assert "Final" in tail and tail.rstrip().endswith("curl mlbsched.run/winprob/CLE/2026-10-10")


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


def test_watch_far_off_game_offers_the_teams_last_game(client, monkeypatch):
    later = (datetime.now(timezone.utc) + timedelta(hours=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    serve(monkeypatch, feed(state="Preview", detailed="Scheduled", start=later))
    asked = []
    monkeypatch.setattr(watch, "last_final", lambda team_id=None: asked.append(team_id) or FINAL_GAME)
    text = plain(client.get("/watch/CLE", headers=CURL).text)
    assert asked == [CLE]
    assert "Replay CLE's last game" in text and "curl -N mlbsched.run/replay/CLE/2026-10-10" in text
    assert "curl mlbsched.run/winprob/CLE/2026-10-10" in text
    assert "replay" not in client.get("/watch/CLE?once=1", headers=CURL).text   # scripts get the frame alone


def test_watch_live_game_offers_no_replay(client, monkeypatch):
    serve(monkeypatch, feed())
    monkeypatch.setattr(watch, "last_final", lambda team_id=None: FINAL_GAME)
    assert "/replay/" not in client.get("/watch/CLE", headers=BROWSER).text


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
    monkeypatch.setattr(watch, "_get_odds", lambda pk: [35.0, 30.0, 20.0, 0.0])   # the whole game's
    r = client.get("/replay/849832?speed=10", headers=CURL)
    shown = r.text.split("\x1b[H")[2:]
    assert len(shown) == 2                                # pre-game timecodes are skipped
    assert "REPLAY" in plain(shown[0]) and "Final" in plain(shown[-1])
    assert "CLE 65%" in plain(shown[0])                   # odds as of that moment, not the end


def test_replay_by_team_and_date(client, monkeypatch):
    final_game = {**GAME, "status": {"abstractGameState": "Final", "detailedState": "Final"}}
    asked = []
    monkeypatch.setattr(watch, "find_team_game", lambda abv, d, n=1: asked.append((abv, d, n)) or final_game)
    monkeypatch.setattr(watch, "fetch_timestamps", lambda pk: ["t0", "t1"])
    monkeypatch.setattr(watch, "REPLAY_STEP_SECONDS", 0)
    frames = {"t0": feed(), "t1": feed(state="Final", detailed="Final")}
    monkeypatch.setattr(watch, "_get_feed", lambda pk, tc=None: frames[tc])
    r = client.get("/replay/cle/2026-10-08?speed=10", headers=CURL)
    assert asked == [("CLE", "2026-10-08", 1)]
    assert "REPLAY" in plain(r.text) and "Final" in plain(r.text.split("\x1b[H")[-1])
    client.get("/replay/CLE/10-08-2026?game=2", headers=CURL)
    assert asked[-1] == ("CLE", "2026-10-08", 2)
    page = re.sub(r"<[^>]+>", "", client.get("/replay/CLE/2026-10-08?game=2", headers=BROWSER).text)
    assert "curl -N mlbsched.run/replay/CLE/2026-10-08?game=2" in page


def test_watch_with_a_date_plays_the_replay(client, monkeypatch):
    final_game = {**GAME, "status": {"abstractGameState": "Final", "detailedState": "Final"}}
    asked = []
    monkeypatch.setattr(watch, "find_team_game", lambda abv, d, n=1: asked.append((abv, d, n)) or final_game)
    monkeypatch.setattr(watch, "fetch_timestamps", lambda pk: ["t0", "t1"])
    monkeypatch.setattr(watch, "REPLAY_STEP_SECONDS", 0)
    frames = {"t0": feed(), "t1": feed(state="Final", detailed="Final")}
    monkeypatch.setattr(watch, "_get_feed", lambda pk, tc=None: frames[tc])
    r = client.get("/watch/cle/2026-10-08?speed=10&game=2", headers=CURL)
    assert asked == [("CLE", "2026-10-08", 2)]
    assert "REPLAY" in plain(r.text) and "Final" in plain(r.text.split("\x1b[H")[-1])
    page = re.sub(r"<[^>]+>", "", client.get("/watch/CLE/2026-10-08", headers=BROWSER).text)
    assert "curl -N mlbsched.run/replay/CLE/2026-10-08" in page   # browsers learn the canonical URL


def test_watch_with_a_date_errors(client, monkeypatch):
    monkeypatch.setattr(watch, "find_team_game", lambda abv, d, n=1: None)
    assert client.get("/watch/ZZZ/2026-10-08", headers=CURL).status_code == 404
    assert client.get("/watch/CLE/someday", headers=CURL).status_code == 404
    assert client.get("/watch/CLE/2026-10-09", headers=CURL).status_code == 404
    monkeypatch.setattr(watch, "find_team_game", lambda abv, d, n=1: GAME)
    r = client.get("/watch/CLE/2026-10-10", headers=CURL)
    assert r.status_code == 400 and "watch it live" in plain(r.text)


def test_replay_by_team_and_date_errors(client, monkeypatch):
    monkeypatch.setattr(watch, "find_team_game", lambda abv, d, n=1: None)
    assert client.get("/replay/ZZZ/2026-10-08", headers=CURL).status_code == 404
    assert client.get("/replay/CLE/someday", headers=CURL).status_code == 404
    r = client.get("/replay/CLE/2026-10-09", headers=CURL)
    assert r.status_code == 404 and "No CLE game on 2026-10-09" in plain(r.text)


def test_find_team_game_skips_postponed_and_picks_the_doubleheader_half(monkeypatch):
    rained_out = {**FINAL_GAME, "gamePk": 1, "status": {"abstractGameState": "Final", "detailedState": "Postponed"}}
    nightcap = {**FINAL_GAME, "gamePk": 2}
    monkeypatch.setattr(watch.sched, "fetch_schedule",
                        lambda d, team_id=None: {"dates": [{"games": [rained_out, FINAL_GAME, nightcap]}]})
    assert watch.find_team_game("CLE", "2026-10-10")["gamePk"] == 849831
    assert watch.find_team_game("CLE", "2026-10-10", 2)["gamePk"] == 2
    assert watch.find_team_game("CLE", "2026-10-10", 3) is None
    assert watch.find_team_game("CLE", "2026-10-10", 0) is None


def test_replay_path():
    assert watch.replay_path(FINAL_GAME) == "/replay/CLE/2026-10-10"
    assert watch.replay_path(FINAL_GAME, "CWS") == "/replay/CWS/2026-10-10"
    nightcap = {**FINAL_GAME, "doubleHeader": "Y", "gameNumber": 2}
    assert watch.replay_path(nightcap) == "/replay/CLE/2026-10-10?game=2"


def test_replay_refuses_a_game_in_progress(client, monkeypatch):
    monkeypatch.setattr(watch, "find_game_by_pk", lambda pk: GAME)
    r = client.get("/replay/849832", headers=CURL)
    assert r.status_code == 400 and "watch it live" in plain(r.text)


# ── replay hint ───────────────────────────────────────────────────────────────
FINAL_GAME = {"gamePk": 849831, "gameType": "D", "seriesDescription": "AL Division Series",
              "seriesGameNumber": 5, "gameDate": "2026-10-11T00:08:00Z", "officialDate": "2026-10-10",
              "status": {"abstractGameState": "Final", "detailedState": "Final"},
              "teams": {"away": {"team": {"id": CWS}, "score": 0}, "home": {"team": {"id": CLE}, "score": 2}}}


class _Resp:
    def __init__(self, data):
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


@pytest.fixture
def schedule(monkeypatch):
    """Stub MLB's schedule for last_final; returns the params of each request."""
    watch._last_final_cache.clear()
    calls = []

    def stub(games):
        def get(url, params=None, timeout=None):
            calls.append(params)
            return _Resp({"dates": [{"games": games}]})
        monkeypatch.setattr(watch.requests, "get", get)
        return calls

    monkeypatch.setattr(watch.sched, "season_dates", lambda year: {"postSeasonEndDate": f"{year}-11-01"})
    yield stub
    watch._last_final_cache.clear()


def test_last_final_is_the_newest_finished_game(schedule, monkeypatch):
    monkeypatch.setattr(watch.sched, "today_et", lambda: date(2026, 10, 22))
    rained_out = {**FINAL_GAME, "gamePk": 1, "gameDate": "2026-10-12T00:00:00Z",
                  "status": {"abstractGameState": "Final", "detailedState": "Postponed"}}
    upcoming = {**FINAL_GAME, "gamePk": 2, "gameDate": "2026-10-24T00:00:00Z",
                "status": {"abstractGameState": "Preview", "detailedState": "Scheduled"}}
    older = {**FINAL_GAME, "gamePk": 3, "gameDate": "2026-10-09T00:00:00Z"}
    calls = schedule([older, FINAL_GAME, rained_out, upcoming])
    assert watch.last_final(CLE)["gamePk"] == 849831
    assert calls[0]["teamId"] == CLE and calls[0]["endDate"] == "2026-10-22"
    watch.last_final(CLE)
    assert len(calls) == 1                                # cached


def test_last_final_in_the_offseason_looks_back_from_the_postseason(schedule, monkeypatch):
    monkeypatch.setattr(watch.sched, "today_et", lambda: date(2027, 2, 10))
    calls = schedule([FINAL_GAME])
    watch.last_final()
    assert "teamId" not in calls[0] and calls[0]["endDate"] == "2026-11-01"


def test_watch_index_offers_a_replay_only_when_nothing_is_live(monkeypatch):
    monkeypatch.setattr(watch, "last_final", lambda team_id=None: FINAL_GAME)
    monkeypatch.setattr(watch.sched, "fetch_schedule", lambda d: {"dates": []})
    monkeypatch.setattr(watch.sched, "live_games_now", lambda: [])
    text = plain(watch.render_index())
    assert "Nothing live right now" in text and "curl -N mlbsched.run/replay/CLE/2026-10-10" in text
    assert "CWS" in text and "ALDS G5" in text
    monkeypatch.setattr(watch.sched, "live_games_now", lambda: [GAME])
    assert "/replay/" not in watch.render_index()
