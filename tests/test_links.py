"""Links in the browser view and the sitemap. Offline: only routes that need no
upstream call are fetched."""

import os
import re
import tempfile
import xml.etree.ElementTree as ET
from datetime import date

import pytest

# Point SQLite at a scratch dir before server (and db) import.
os.environ.setdefault("MLBSCHED_DATA_DIR", tempfile.mkdtemp(prefix="mlbsched-test-"))

from fastapi.testclient import TestClient   # noqa: E402

import leaders                               # noqa: E402
import mlbsched as sched                     # noqa: E402
import server                                # noqa: E402

CURL = {"User-Agent": "curl/8.4.0"}
BROWSER = {"User-Agent": "Mozilla/5.0"}
HREF = re.compile(r'<a href="([^"]*)">')


@pytest.fixture(scope="module")
def client():
    with TestClient(server.app) as c:
        yield c


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(server, "today_et", lambda: date(2026, 10, 9))


def pre_hrefs(page: str) -> list[str]:
    """hrefs inside the <pre> block (the footer's own link is outside it)."""
    return HREF.findall(page.split("<pre>", 1)[1].split("</pre>", 1)[0])


# ── linkify ───────────────────────────────────────────────────────────────────

def test_concrete_paths_link_as_is():
    assert server.ansi_to_html("Try: curl mlbsched.run/teams") == \
        'Try: curl <a href="/teams">mlbsched.run/teams</a>'
    out = server.ansi_to_html("curl mlbsched.run  and  mlbsched.run/postseason/2015.")
    assert HREF.findall(out) == ["/", "/postseason/2015"]
    assert out.endswith("</a>.")                      # sentence period stays outside


def test_link_keeps_colors_and_spans_whole():
    out = server.ansi_to_html("\x1b[90mTry: curl mlbsched.run/help\x1b[0m")
    assert out == ('<span style="color:#6e7681">Try: curl </span>'
                   '<a href="/help"><span style="color:#6e7681">mlbsched.run/help</span></a>')
    # A mention that runs across a color change (about page: "mlbsched.run/" + orange NYM)
    out = server.ansi_to_html("curl mlbsched.run/\x1b[38;5;208mNYM\x1b[0m   One team")
    assert out == ('curl <a href="/NYM">mlbsched.run/<span style="color:#ff7b00">NYM</span></a>'
                   '   One team')


def test_templated_paths_link_to_examples():
    cases = {
        "mlbsched.run/<TEAM>":              "/NYM",
        "mlbsched.run/<TEAM>/next":         "/NYM/next",
        "mlbsched.run/<TEAM>/<DATE>":       "/NYM/2026-10-09",
        "mlbsched.run/<DATE>":              "/2026-10-09",
        "mlbsched.run/h2h/<TEAM>/<TEAM>":   "/h2h/NYM/PHI",
        "mlbsched.run/box/<TEAM>/random":   "/box/NYM/random",
        "mlbsched.run/player/<NAME>":       "/player/lindor",
        "mlbsched.run/leaders/<stat>":      "/leaders/ops",
        "mlbsched.run/postseason/<YEAR>":   "/postseason/2015",
        "mlbsched.run/ical/<TEAM>.ics":     "/ical/NYM.ics",
        "webcal://mlbsched.run/ical/<TEAM>.ics": "webcal://mlbsched.run/ical/NYM.ics",
        "https://mlbsched.run/standings":   "/standings",
    }
    for mention, href in cases.items():
        out = server.ansi_to_html(f"  {mention}  ")
        assert HREF.findall(out) == [href], mention
        assert "&lt;" in out or "<" not in mention   # templates still read as <TEAM>


def test_untrusted_or_unknown_paths_never_link():
    for text in (
        "Unknown: mlbsched.run//evil.com",           # no protocol-relative hrefs
        'Unknown: mlbsched.run/evil"><script>',      # first segment isn't ours
        "mlbsched.run/metrics",                      # token-gated, never advertised
        "mlbsched.run/<FOO>",                        # template we have no example for
        "mlbsched.run/box/<TEAM>/<DATE>",            # no date that's right everywhere
        "mlbsched.run/wp/<TEAM>/<DATE>",
        "x.mlbsched.run/teams",                      # someone else's host
        "foo@mlbsched.run",
    ):
        out = server.ansi_to_html(text)
        assert "<a " not in out, text
    assert "&lt;script&gt;" in server.ansi_to_html('mlbsched.run/evil"><script>')


def test_404_echo_cannot_inject_links(client):
    # "Unknown: <segment>" echoes the request; it must come back as text.
    r = client.get('/<a href="evil.example">zzz', headers=BROWSER)
    assert r.status_code == 404
    assert pre_hrefs(r.text) == ["/teams", "/help"]
    assert "&lt;a href=&quot;evil.example&quot;&gt;zzz" in r.text


def test_browser_help_is_clickable_and_curl_is_unchanged(client):
    html = client.get("/help", headers=BROWSER).text
    hrefs = pre_hrefs(html)
    for href in ("/", "/standings", "/NYM", "/NYM/next", "/box/NYM/random", "/teams"):
        assert href in hrefs
    assert "pre a" in html and "color: inherit" in html
    text = client.get("/help", headers=CURL).text
    assert text == sched.render_help()
    assert "<a " not in text and "mlbsched.run/standings" in text


def test_link_roots_follow_the_routes():
    roots = server._link_roots()
    assert {"standings", "box", "ical", "help", "postseason"} <= roots
    assert "metrics" not in roots


# ── sitemap ───────────────────────────────────────────────────────────────────

def test_sitemap(client):
    r = client.get("/sitemap.xml")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/xml")
    assert r.headers["cache-control"] == "public, max-age=86400"
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    locs = [e.text for e in ET.fromstring(r.content).findall("s:url/s:loc", ns)]
    assert len(locs) == len(set(locs)) == (
        len(server._SITEMAP_PAGES) + len(sched.TEAMS) + len(leaders.ALL_STATS))
    assert all(loc.startswith("https://mlbsched.run/") for loc in locs)
    for page in ("/", "/standings", "/NYM", "/ATH", "/leaders/ops", "/birthdays/all"):
        assert "https://mlbsched.run" + page in locs
    for loc in locs:                                  # every entry is a page we serve
        first = loc.removeprefix("https://mlbsched.run/").split("/")[0]
        assert not first or first in server._link_roots() or first in sched.TEAMS


def test_robots_points_at_sitemap(client):
    assert "Sitemap: https://mlbsched.run/sitemap.xml" in client.get("/robots.txt").text
