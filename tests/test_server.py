"""HTTP-level tests through FastAPI's TestClient. Only routes that need no
upstream call are exercised, so the suite runs offline and in CI."""

import os
import re
import tempfile

import pytest

# Point SQLite at a scratch dir before server (and db) import.
os.environ["MLBSCHED_DATA_DIR"] = tempfile.mkdtemp(prefix="mlbsched-test-")

from fastapi.testclient import TestClient   # noqa: E402

import server                                # noqa: E402

ANSI = re.compile(r"\x1b\[[0-9;]*m")
CURL = {"User-Agent": "curl/8.4.0"}
BROWSER = {"User-Agent": "Mozilla/5.0"}


@pytest.fixture(scope="module")
def client():
    with TestClient(server.app) as c:
        yield c


def test_content_negotiation(client):
    text = client.get("/teams", headers=CURL)
    html = client.get("/teams", headers=BROWSER)
    assert text.status_code == 200 and text.headers["content-type"].startswith("text/plain")
    assert "\x1b[" in text.text                       # ANSI for terminals
    assert html.headers["content-type"].startswith("text/html")
    assert "<pre>" in html.text and "\x1b[" not in html.text
    assert 'property="og:image"' in html.text


def test_ansi_to_html_escapes_and_colors():
    out = server.ansi_to_html("\x1b[1m\x1b[94mNYM\x1b[0m <b>")
    assert '<span style="color:#58a6ff;font-weight:bold">NYM</span>' in out
    assert "&lt;b&gt;" in out


def test_cache_headers(client):
    r = client.get("/teams", headers=CURL)
    assert r.headers["cache-control"] == "private, max-age=30"
    # Newer Starlette's CORS middleware appends "Origin"; ours must be in the list.
    assert "User-Agent" in [v.strip() for v in r.headers["vary"].split(",")]
    assert client.get("/og.png").headers.get("cache-control", "").startswith("public") or client.get("/og.png").status_code == 404


def test_boilerplate_routes(client):
    assert client.get("/favicon.ico").status_code == 204
    robots = client.get("/robots.txt")
    assert robots.status_code == 200 and "Disallow: /metrics" in robots.text


def test_unknown_segment_is_404_with_hint(client):
    r = client.get("/zzz", headers=CURL)
    assert r.status_code == 404 and "Try: curl mlbsched.run/teams" in ANSI.sub("", r.text)
    r = client.get("/api/zzz")
    assert r.status_code == 404 and r.json() == {"error": "Unknown team: ZZZ"}
    assert client.get("/h2h/NYM/NYM", headers=CURL).status_code == 400
    assert client.get("/api/h2h/NYM/NYM").json() == {"error": "Pick two different teams."}
    assert client.get("/api/h2h/NYM/ZZZ").status_code == 404


def test_bad_query_param_is_plain_text_422(client):
    r = client.get("/streaks?min=abc", headers=CURL)
    assert r.status_code == 422
    assert "Invalid value for: min" in ANSI.sub("", r.text)
    assert client.get("/api/streaks?min=abc").status_code == 422


def test_metrics_requires_token(client, monkeypatch):
    monkeypatch.delenv("MLBSCHED_METRICS_TOKEN", raising=False)
    assert client.get("/metrics").status_code == 503
    monkeypatch.setenv("MLBSCHED_METRICS_TOKEN", "s3cret")
    assert client.get("/metrics").status_code == 401
    assert client.get("/metrics", headers={"Authorization": "Bearer nope"}).status_code == 401
    r = client.get("/metrics", headers={"Authorization": "Bearer s3cret", **CURL})
    assert r.status_code == 200 and r.text.startswith("mlbsched metrics")
    assert r.headers["cache-control"] == "no-store"


def test_metrics_daily_counts_skip_bot_scanners(client, monkeypatch):
    import db
    from datetime import datetime, timedelta, timezone
    # Three days back: inside /metrics' 30-day window, but a date the live request
    # logging (which only writes today) never touches, so the counts are exact.
    day = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%d")
    rows = [("/NYM", "curl", "a"), ("/NYM", "browser", "b"),
            ("/wp-login.php", "browser", "c"), ("/.env", "curl", "d"), ("/.git/config", "curl", "e"),
            ("/admin/elmah.axd", "browser", "f"), ("/.npmrc", "curl", "g")]
    conn = db._conn()
    conn.executemany(
        "INSERT INTO requests (ts, date, path, client, ip_hash) VALUES (?, ?, ?, ?, ?)",
        [(f"{day}T12:00:00", day, path, cl, ip) for path, cl, ip in rows],
    )
    conn.commit()
    monkeypatch.setenv("MLBSCHED_METRICS_TOKEN", "s3cret")
    r = client.get("/metrics", headers={"Authorization": "Bearer s3cret", **CURL})
    line = next(l for l in r.text.splitlines() if l.startswith(day))
    assert line.split()[1:] == ["2", "2", "1", "1"]     # requests, unique, curl, browser: bots excluded


def test_referrer_site_keeps_only_the_domain():
    import db
    assert db.referrer_site("https://www.reddit.com/r/baseball/comments/abc/") == "reddit.com"
    assert db.referrer_site("https://news.ycombinator.com/item?id=1") == "news.ycombinator.com"
    assert db.referrer_site("https://mlbsched.run/watch") is None       # clicks within the site
    assert db.referrer_site("") is None and db.referrer_site("not a url") is None
    assert db.referrer_site("http://[::1") is None                       # malformed, no raise


def test_init_db_adds_referrer_to_an_older_database(tmp_path, monkeypatch):
    import sqlite3
    import db
    old = tmp_path / "metrics.db"
    conn = sqlite3.connect(old)
    conn.execute("CREATE TABLE requests (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, "
                 "date TEXT NOT NULL, path TEXT NOT NULL, client TEXT NOT NULL, ip_hash TEXT NOT NULL)")
    conn.commit()
    conn.close()
    monkeypatch.setattr(db, "DB_PATH", old)
    monkeypatch.setattr(db, "_local", type(db._local)())
    db.init_db()
    db.init_db()                                                         # second start is a no-op
    db.log_request("/NYM", "1.2.3.4", "Mozilla/5.0", "https://www.reddit.com/r/baseball/")
    assert db.query("SELECT path, referrer FROM requests")[0]["referrer"] == "reddit.com"


def test_metrics_lists_where_visitors_came_from(client, monkeypatch):
    import db
    from datetime import datetime, timedelta, timezone
    day = (datetime.now(timezone.utc) - timedelta(days=4)).strftime("%Y-%m-%d")
    rows = [("/watch/MIL", "h", "reddit.com"), ("/", "h", "reddit.com"), ("/", "i", "reddit.com"),
            ("/", "j", "news.ycombinator.com"), ("/<script>", "k", "<b>evil.example</b>")]
    conn = db._conn()
    conn.executemany(
        "INSERT INTO requests (ts, date, path, client, ip_hash, referrer) VALUES (?, ?, ?, 'browser', ?, ?)",
        [(f"{day}T12:00:00", day, path, ip, ref) for path, ip, ref in rows],
    )
    conn.commit()
    monkeypatch.setenv("MLBSCHED_METRICS_TOKEN", "s3cret")
    auth = {"Authorization": "Bearer s3cret"}
    text = client.get("/metrics", headers={**auth, **CURL}).text
    sources = text.split("Where visitors came from")[1].split("Top paths")[0]
    assert re.search(r"reddit\.com\s+2\s+3", sources)                 # 2 visitors, 3 requests
    assert sources.index("reddit.com") < sources.index("news.ycombinator.com")
    page = client.get("/metrics", headers={**auth, **BROWSER}).text
    assert "<td>reddit.com</td>" in page
    assert "<script>" not in page and "&lt;script&gt;" in page          # logged paths are escaped
    assert "&lt;b&gt;evil.example" in page


def test_requests_log_their_referrer(client):
    import db
    import time
    client.get("/teams", headers={**CURL, "Referer": "https://www.linkedin.com/feed/"})
    for _ in range(50):                                                   # logging runs off the loop
        if db.query("SELECT 1 FROM requests WHERE referrer = 'linkedin.com'"):
            break
        time.sleep(0.02)
    assert db.query("SELECT path FROM requests WHERE referrer = 'linkedin.com'")[0]["path"] == "/teams"


def test_help_about_random_offline(client):
    for path in ("/help", "/about", "/random", "/ical"):
        assert client.get(path, headers=CURL).status_code == 200, path
    assert client.get("/api/about").json()["name"] == "mlbsched.run"
    assert client.get("/api/random").json()["team"] == "NYM"
    assert client.get("/api/teams").json()["teams"][0]["team"] == "ARI"


def test_ical_route_shape(client, monkeypatch):
    import ical
    monkeypatch.setattr(ical, "_season_games_cache", {})
    monkeypatch.setattr(ical, "_fetch_season_games", lambda tid, y: [])
    r = client.get("/ical/NYM.ics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/calendar")
    assert r.headers["cache-control"] == "public, max-age=1800"
    assert client.get("/ical/ZZZ.ics").status_code == 404


def test_ical_outage_answers_503_not_an_empty_calendar(client, monkeypatch):
    # An empty feed would make calendar apps delete the team's games; a 503 keeps them.
    import ical
    import requests

    def down(team_id, year):
        raise requests.ConnectionError("MLB down")
    monkeypatch.setattr(ical, "_season_games_cache", {})
    monkeypatch.setattr(ical, "_fetch_season_games", down)
    r = client.get("/ical/NYM.ics", headers=CURL)
    assert r.status_code == 503 and r.headers["retry-after"] == "30"


def test_client_ip_prefers_forwarded(client):
    class Req:
        def __init__(self, headers): self.headers = headers; self.client = type("c", (), {"host": "10.0.0.1"})()
    assert server.get_client_ip(Req({"x-forwarded-for": "1.2.3.4, 5.6.7.8"})) == "1.2.3.4"
    assert server.get_client_ip(Req({})) == "10.0.0.1"
