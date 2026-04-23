"""Sportsbook odds — The Odds API client with SQLite cache and best-price logic."""

import io
import json
import os
from datetime import datetime, timezone as _UTC
from zoneinfo import ZoneInfo

import requests

import db
import mlbsched as sched
from mlbsched import (
    BOLD, DIM, RESET, RED, GREEN, YELLOW, BLUE, CYAN, WHITE, GRAY,
    TEAMS, team_color, fmt_team, fmt_game_time, today_et, ET,
)

ODDS_API_BASE = "https://api.the-odds-api.com/v4"
SPORT_KEY     = "baseball_mlb"
REGIONS       = "us,us2"          # us2 adds ESPN BET + Fanatics
MARKETS       = "h2h,spreads,totals"
ODDS_FORMAT   = "american"

CACHE_TTL_SECONDS = 900           # 15 minutes

# Bookmaker keys from The Odds API → display name. Limited to NY-licensed mobile
# sportsbooks (as of 2026); keys not returned by the API are silently ignored.
NY_BOOKS: dict[str, str] = {
    "draftkings":     "DraftKings",
    "fanduel":        "FanDuel",
    "betmgm":         "BetMGM",
    "williamhill_us": "Caesars",
    "betrivers":      "BetRivers",
    "espnbet":        "ESPN BET",
    "fanatics":       "Fanatics",
}

# Short tags for compact rendering
BOOK_TAG: dict[str, str] = {
    "draftkings":     "DK",
    "fanduel":        "FD",
    "betmgm":         "MGM",
    "williamhill_us": "CZR",
    "betrivers":      "BR",
    "espnbet":        "ESPN",
    "fanatics":       "FAN",
}

FULL_NAME_TO_ABV: dict[str, str] = {name: abv for abv, (_, name, _) in TEAMS.items()}


# ── API + cache ───────────────────────────────────────────────────────────────

class OddsApiError(Exception):
    pass


def _fetch_from_api(api_key: str) -> tuple[list[dict], int | None]:
    """Fetch live odds. Returns (events, x-requests-remaining)."""
    resp = requests.get(
        f"{ODDS_API_BASE}/sports/{SPORT_KEY}/odds",
        params={
            "apiKey":     api_key,
            "regions":    REGIONS,
            "markets":    MARKETS,
            "oddsFormat": ODDS_FORMAT,
        },
        timeout=10,
    )
    if resp.status_code == 429:
        raise OddsApiError("rate_limited")
    if resp.status_code == 401:
        raise OddsApiError("unauthorized")
    resp.raise_for_status()
    remaining = resp.headers.get("x-requests-remaining")
    try:
        remaining_int = int(remaining) if remaining is not None else None
    except ValueError:
        remaining_int = None
    return resp.json(), remaining_int


def _cache_age_seconds(fetched_at_iso: str) -> float:
    dt = datetime.fromisoformat(fetched_at_iso)
    return (datetime.now(_UTC.utc) - dt).total_seconds()


def get_odds_events() -> tuple[list[dict], dict]:
    """Return (events, meta). Meta: {fetched_at, requests_remaining, source: fresh|cache|stale, error?}."""
    api_key = os.environ.get("ODDS_API_KEY")
    cached  = db.read_latest_odds_cache()

    if cached is not None and _cache_age_seconds(cached["fetched_at"]) < CACHE_TTL_SECONDS:
        return json.loads(cached["data"]), {
            "fetched_at":         cached["fetched_at"],
            "requests_remaining": cached["requests_remaining"],
            "source":             "cache",
        }

    if not api_key:
        if cached is not None:
            return json.loads(cached["data"]), {
                "fetched_at":         cached["fetched_at"],
                "requests_remaining": cached["requests_remaining"],
                "source":             "stale",
                "error":              "missing_api_key",
            }
        return [], {"source": "stale", "error": "missing_api_key"}

    try:
        events, remaining = _fetch_from_api(api_key)
        db.write_odds_cache(json.dumps(events), remaining)
        return events, {
            "fetched_at":         datetime.now(_UTC.utc).isoformat(),
            "requests_remaining": remaining,
            "source":             "fresh",
        }
    except (OddsApiError, requests.RequestException) as e:
        err = str(e) if isinstance(e, OddsApiError) else "network_error"
        if cached is not None:
            return json.loads(cached["data"]), {
                "fetched_at":         cached["fetched_at"],
                "requests_remaining": cached["requests_remaining"],
                "source":             "stale",
                "error":              err,
            }
        return [], {"source": "stale", "error": err}


# ── Best-price logic ──────────────────────────────────────────────────────────

def _best_prices(event: dict) -> dict:
    """Per market/outcome, pick the highest American price across NY books."""
    best: dict[str, dict] = {"h2h": {}, "spreads": {}, "totals": {}}
    for bm in event.get("bookmakers", []):
        book_key = bm.get("key")
        if book_key not in NY_BOOKS:
            continue
        for market in bm.get("markets", []):
            mkey = market.get("key")
            if mkey not in best:
                continue
            for out in market.get("outcomes", []):
                price = out.get("price")
                name  = out.get("name")
                if price is None or name is None:
                    continue
                current = best[mkey].get(name)
                if current is None or price > current["price"]:
                    best[mkey][name] = {
                        "price": price,
                        "point": out.get("point"),
                        "book":  book_key,
                    }
    return best


# ── Formatting helpers ────────────────────────────────────────────────────────

def _fmt_american(price: int | None) -> str:
    if price is None:
        return "  —  "
    return f"+{price}" if price >= 0 else str(price)


def _fmt_point(point: float | None, with_sign: bool = True) -> str:
    if point is None:
        return ""
    if with_sign and point > 0:
        return f"+{point:g}"
    return f"{point:g}"


def _book_tag(book_key: str) -> str:
    return BOOK_TAG.get(book_key, book_key[:4].upper())


# ── Renderers ─────────────────────────────────────────────────────────────────

def _render_event(event: dict, out, tz: ZoneInfo | None = None):
    def p(s=""):
        print(s, file=out)

    home_full = event.get("home_team", "")
    away_full = event.get("away_team", "")
    home_abv  = FULL_NAME_TO_ABV.get(home_full, "???")
    away_abv  = FULL_NAME_TO_ABV.get(away_full, "???")

    commence = event.get("commence_time", "")
    game_time = fmt_game_time(commence, tz) if commence else ""

    header = f"  {fmt_team(away_abv)} {DIM}@{RESET} {fmt_team(home_abv)}"
    if game_time:
        header += f"   {CYAN}{game_time}{RESET}"
    p(header)

    best = _best_prices(event)

    if not any(best.values()):
        p(f"    {GRAY}No NY-book odds available.{RESET}")
        p()
        return

    # One row per team
    for abv, full in [(away_abv, away_full), (home_abv, home_full)]:
        ml = best["h2h"].get(full)
        rl = best["spreads"].get(full)

        ml_str = (
            f"ML {_fmt_american(ml['price']):>5} {GRAY}({_book_tag(ml['book'])}){RESET}"
            if ml else f"{GRAY}ML   —{RESET}"
        )
        rl_str = (
            f"RL {_fmt_point(rl['point']):>4} {_fmt_american(rl['price']):>5} "
            f"{GRAY}({_book_tag(rl['book'])}){RESET}"
            if rl else f"{GRAY}RL   —{RESET}"
        )
        p(f"    {fmt_team(abv)}  {ml_str}   {rl_str}")

    # Totals: one line for Over, one for Under
    over  = best["totals"].get("Over")
    under = best["totals"].get("Under")
    if over or under:
        if over:
            o_str = (
                f"O {_fmt_point(over['point'], with_sign=False):>3}  "
                f"{_fmt_american(over['price']):>5} {GRAY}({_book_tag(over['book'])}){RESET}"
            )
        else:
            o_str = f"{GRAY}O   —{RESET}"
        if under:
            u_str = (
                f"U {_fmt_point(under['point'], with_sign=False):>3}  "
                f"{_fmt_american(under['price']):>5} {GRAY}({_book_tag(under['book'])}){RESET}"
            )
        else:
            u_str = f"{GRAY}U   —{RESET}"
        p(f"    {GRAY}TOT{RESET}  {o_str}   {u_str}")

    p()


def _filter_events_today(events: list[dict], tz: ZoneInfo | None = None) -> list[dict]:
    """Keep only games scheduled for today (ET)."""
    target = today_et().strftime("%Y-%m-%d")
    out = []
    for e in events:
        ct = e.get("commence_time", "")
        if not ct:
            continue
        try:
            dt_utc = datetime.strptime(ct, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_UTC.utc)
            et_date = dt_utc.astimezone(ET).strftime("%Y-%m-%d")
        except ValueError:
            continue
        if et_date == target:
            out.append(e)
    return out


def _render_meta_line(meta: dict, out):
    def p(s=""):
        print(s, file=out)

    if meta.get("error") == "missing_api_key":
        p(f"  {YELLOW}Odds feed not configured — set ODDS_API_KEY to enable.{RESET}")
        return
    if meta.get("error") == "rate_limited":
        p(f"  {YELLOW}Odds feed is rate-limited — showing cached data.{RESET}")
    elif meta.get("error") == "unauthorized":
        p(f"  {RED}Odds API key rejected — check ODDS_API_KEY.{RESET}")
    elif meta.get("error"):
        p(f"  {YELLOW}Odds feed error ({meta['error']}) — showing cached data.{RESET}")

    if meta.get("source") in ("cache", "stale") and meta.get("fetched_at"):
        try:
            dt = datetime.fromisoformat(meta["fetched_at"]).astimezone(ET)
            p(f"  {GRAY}Prices cached at {dt.strftime('%-I:%M %p %Z')}.{RESET}")
        except Exception:
            pass


def render_odds(team_abv: str | None = None, out=None, tz: ZoneInfo | None = None) -> str:
    buf = io.StringIO()
    _out = out or buf

    def p(s=""):
        print(s, file=_out)

    today = today_et()
    label = today.strftime("%A, %B %-d, %Y")

    events, meta = get_odds_events()
    events = _filter_events_today(events, tz)

    if team_abv:
        abv = team_abv.upper()
        full = TEAMS.get(abv, (None, None, None))[1]
        if full:
            events = [e for e in events if abv in (
                FULL_NAME_TO_ABV.get(e.get("home_team", ""), ""),
                FULL_NAME_TO_ABV.get(e.get("away_team", ""), ""),
            )]
        title = f"{BOLD}{team_color(abv)}{TEAMS.get(abv, (None, abv, None))[1]}{RESET} — {BOLD}{WHITE}{label}{RESET}"
    else:
        title = f"{BOLD}{CYAN}NY Sportsbook Odds{RESET} — {BOLD}{WHITE}{label}{RESET}"

    p()
    p(f"  {title}")
    p(f"  {GRAY}{'─' * 60}{RESET}")

    if not events:
        if meta.get("error") == "missing_api_key":
            _render_meta_line(meta, _out)
        else:
            p(f"  {GRAY}No games with NY-book odds for this selection.{RESET}")
            _render_meta_line(meta, _out)
        p()
        _render_footer(_out)
        return buf.getvalue()

    for event in events:
        _render_event(event, _out, tz=tz)

    _render_meta_line(meta, _out)
    p()
    _render_footer(_out)
    return buf.getvalue()


def _render_footer(out):
    print(
        f"  {GRAY}Best price per market across NY-licensed books. "
        f"Odds via the-odds-api.com.{RESET}",
        file=out,
    )
    print(
        f"  {GRAY}Problem gambling? Call 1-800-GAMBLER.{RESET}",
        file=out,
    )


# ── JSON API ──────────────────────────────────────────────────────────────────

def build_odds_json(event: dict, tz: ZoneInfo | None = None) -> dict:
    home_full = event.get("home_team", "")
    away_full = event.get("away_team", "")
    home_abv  = FULL_NAME_TO_ABV.get(home_full, "???")
    away_abv  = FULL_NAME_TO_ABV.get(away_full, "???")
    best = _best_prices(event)

    def wrap(entry, include_point=False):
        if not entry:
            return None
        out = {
            "price": entry["price"],
            "book":  entry["book"],
            "book_name": NY_BOOKS.get(entry["book"], entry["book"]),
        }
        if include_point:
            out["point"] = entry["point"]
        return out

    return {
        "away":      away_abv,
        "home":      home_abv,
        "commence":  fmt_game_time(event.get("commence_time", ""), tz) or None,
        "commence_utc": event.get("commence_time"),
        "moneyline": {
            "away": wrap(best["h2h"].get(away_full)),
            "home": wrap(best["h2h"].get(home_full)),
        },
        "run_line": {
            "away": wrap(best["spreads"].get(away_full), include_point=True),
            "home": wrap(best["spreads"].get(home_full), include_point=True),
        },
        "total": {
            "over":  wrap(best["totals"].get("Over"),  include_point=True),
            "under": wrap(best["totals"].get("Under"), include_point=True),
        },
    }
