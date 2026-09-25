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


def test_help_about_random_offline(client):
    for path in ("/help", "/about", "/random", "/ical"):
        assert client.get(path, headers=CURL).status_code == 200, path
    assert client.get("/api/about").json()["name"] == "mlbsched.run"
    assert client.get("/api/random").json()["team"] == "NYM"
    assert client.get("/api/teams").json()["teams"][0]["team"] == "ARI"


def test_ical_route_shape(client, monkeypatch):
    import ical
    monkeypatch.setattr(ical, "_fetch_season_games", lambda tid, y: [])
    r = client.get("/ical/NYM.ics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/calendar")
    assert r.headers["cache-control"] == "public, max-age=1800"
    assert client.get("/ical/ZZZ.ics").status_code == 404


def test_client_ip_prefers_forwarded(client):
    class Req:
        def __init__(self, headers): self.headers = headers; self.client = type("c", (), {"host": "10.0.0.1"})()
    assert server.get_client_ip(Req({"x-forwarded-for": "1.2.3.4, 5.6.7.8"})) == "1.2.3.4"
    assert server.get_client_ip(Req({})) == "10.0.0.1"
