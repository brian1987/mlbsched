"""Best-bets — flag pricing edges using no-vig multi-book consensus."""

import io
from datetime import datetime, timedelta, timezone as _UTC
from zoneinfo import ZoneInfo

import odds
from mlbsched import (
    BOLD, DIM, RESET, RED, GREEN, YELLOW, CYAN, WHITE, GRAY,
    TEAMS, team_color, fmt_team, fmt_game_time, today_et,
)

MARKET_KEYS = ("h2h", "spreads", "totals")

MIN_DISPLAY_EV    = 0.01   # 1% — below this is noise
STRONG_EDGE_EV    = 0.03   # 3% — worth calling out
SUSPICIOUS_EV     = 0.05   # 5% — often a stale line
TOP_N             = 5
MIN_LEAD_MINUTES  = 15     # skip games starting sooner than this (steam already moved)


def _filter_upcoming(events: list[dict]) -> list[dict]:
    """Drop events already underway or starting within MIN_LEAD_MINUTES."""
    cutoff = datetime.now(_UTC.utc) + timedelta(minutes=MIN_LEAD_MINUTES)
    out: list[dict] = []
    for e in events:
        ct = e.get("commence_time", "")
        if not ct:
            continue
        try:
            dt_utc = datetime.strptime(ct, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_UTC.utc)
        except ValueError:
            continue
        if dt_utc >= cutoff:
            out.append(e)
    return out


# ── Math ──────────────────────────────────────────────────────────────────────

def american_to_prob(american: int) -> float:
    if american >= 0:
        return 100.0 / (american + 100)
    return abs(american) / (abs(american) + 100.0)


def prob_to_american(p: float) -> int:
    if p <= 0 or p >= 1:
        return 0
    if p > 0.5:
        return -round(p / (1 - p) * 100)
    return round((1 - p) / p * 100)


def ev_fraction(p: float, american: int) -> float:
    """Expected return per $1 wagered, as a fraction (0.05 = +5% EV)."""
    decimal = 1 + (american / 100.0 if american >= 0 else 100.0 / abs(american))
    return p * (decimal - 1) - (1 - p)


def _no_vig_pair(p_a: float, p_b: float) -> tuple[float, float]:
    total = p_a + p_b
    if total <= 0:
        return 0.0, 0.0
    return p_a / total, p_b / total


# ── Consensus ─────────────────────────────────────────────────────────────────

def consensus_probs(event: dict) -> dict[str, dict[str, float]]:
    """Return {market: {outcome_name: avg no-vig fair prob across NY books}}."""
    sums: dict[str, dict[str, float]] = {m: {} for m in MARKET_KEYS}
    counts: dict[str, dict[str, int]] = {m: {} for m in MARKET_KEYS}

    for bm in event.get("bookmakers", []):
        if bm.get("key") not in odds.NY_BOOKS:
            continue
        for market in bm.get("markets", []):
            mkey = market.get("key")
            if mkey not in sums:
                continue
            outcomes = market.get("outcomes", [])
            if len(outcomes) != 2:
                continue
            a, b = outcomes
            a_name, a_price = a.get("name"), a.get("price")
            b_name, b_price = b.get("name"), b.get("price")
            if not (a_name and b_name and a_price is not None and b_price is not None):
                continue
            a_fair, b_fair = _no_vig_pair(
                american_to_prob(a_price), american_to_prob(b_price)
            )
            sums[mkey][a_name]   = sums[mkey].get(a_name, 0.0) + a_fair
            counts[mkey][a_name] = counts[mkey].get(a_name, 0) + 1
            sums[mkey][b_name]   = sums[mkey].get(b_name, 0.0) + b_fair
            counts[mkey][b_name] = counts[mkey].get(b_name, 0) + 1

    out: dict[str, dict[str, float]] = {m: {} for m in MARKET_KEYS}
    for mkey in MARKET_KEYS:
        for name, s in sums[mkey].items():
            c = counts[mkey][name]
            if c > 0:
                out[mkey][name] = s / c
    return out


# ── Edges ─────────────────────────────────────────────────────────────────────

def find_edges(event: dict) -> list[dict]:
    best = odds._best_prices(event)
    consensus = consensus_probs(event)

    home_full = event.get("home_team", "")
    away_full = event.get("away_team", "")
    home_abv = odds.FULL_NAME_TO_ABV.get(home_full, "???")
    away_abv = odds.FULL_NAME_TO_ABV.get(away_full, "???")

    edges: list[dict] = []
    for mkey in MARKET_KEYS:
        for outcome_name, entry in best[mkey].items():
            p = consensus[mkey].get(outcome_name)
            if p is None:
                continue
            ev = ev_fraction(p, entry["price"])
            edges.append({
                "market":       mkey,
                "outcome":      outcome_name,
                "home_abv":     home_abv,
                "away_abv":     away_abv,
                "home_full":    home_full,
                "away_full":    away_full,
                "price":        entry["price"],
                "point":        entry.get("point"),
                "book":         entry["book"],
                "fair_price":   prob_to_american(p),
                "fair_prob":    p,
                "ev":           ev,
                "commence":     event.get("commence_time", ""),
                "event_id":     event.get("id"),
            })
    return edges


# ── Rendering ─────────────────────────────────────────────────────────────────

def _edge_label(edge: dict) -> str:
    mkey = edge["market"]
    outcome = edge["outcome"]
    point = edge.get("point")
    if mkey == "h2h":
        abv = odds.FULL_NAME_TO_ABV.get(outcome, "???")
        return f"{fmt_team(abv)} ML      "
    if mkey == "spreads":
        abv = odds.FULL_NAME_TO_ABV.get(outcome, "???")
        pt = odds._fmt_point(point) if point is not None else ""
        return f"{fmt_team(abv)} RL {pt:<4}"
    if mkey == "totals":
        pt = odds._fmt_point(point, with_sign=False) if point is not None else ""
        side = "O" if outcome.lower() == "over" else "U"
        return f"{GRAY}TOT{RESET} {side} {pt:<4}"
    return f"{mkey} {outcome}"


def _render_game_block(event_id: str, edges_in_game: list[dict], out, tz: ZoneInfo | None):
    first = edges_in_game[0]
    header = (
        f"  {fmt_team(first['away_abv'])} {DIM}@{RESET} {fmt_team(first['home_abv'])}"
    )
    gt = fmt_game_time(first["commence"], tz) if first["commence"] else ""
    if gt:
        header += f"    {CYAN}{gt}{RESET}"
    print(header, file=out)

    for edge in edges_in_game:
        label = _edge_label(edge)
        price_str = odds._fmt_american(edge["price"])
        fair_str  = odds._fmt_american(edge["fair_price"])
        book_tag  = odds._book_tag(edge["book"])
        ev_pct    = edge["ev"] * 100

        if edge["ev"] >= SUSPICIOUS_EV:
            tag = f"  {YELLOW}← check news{RESET}"
        elif edge["ev"] >= STRONG_EDGE_EV:
            tag = f"  {GREEN}← strong{RESET}"
        else:
            tag = ""

        ev_color = GREEN if edge["ev"] >= STRONG_EDGE_EV else WHITE
        print(
            f"    {label}  {price_str:>5} {GRAY}({book_tag}){RESET}   "
            f"{GRAY}fair{RESET} {fair_str:>5}   "
            f"{ev_color}{ev_pct:+.1f}% EV{RESET}{tag}",
            file=out,
        )
    print(file=out)


def render_bestbets(team_abv: str | None = None, out=None, tz: ZoneInfo | None = None) -> str:
    buf = io.StringIO()
    _out = out or buf

    def p(s=""):
        print(s, file=_out)

    today = today_et()
    label = today.strftime("%A, %B %-d, %Y")

    events, meta = odds.get_odds_events()
    events = odds._filter_events_today(events, tz)
    events = _filter_upcoming(events)

    if team_abv:
        abv = team_abv.upper()
        events = [e for e in events if abv in (
            odds.FULL_NAME_TO_ABV.get(e.get("home_team", ""), ""),
            odds.FULL_NAME_TO_ABV.get(e.get("away_team", ""), ""),
        )]
        title = f"{BOLD}{team_color(abv)}{TEAMS.get(abv, (None, abv, None))[1]}{RESET} — {BOLD}{WHITE}Best Bets{RESET} — {BOLD}{WHITE}{label}{RESET}"
    else:
        title = f"{BOLD}{CYAN}Best Bets{RESET} — {BOLD}{WHITE}{label}{RESET}"

    p()
    p(f"  {title}")
    p(f"  {GRAY}{'─' * 68}{RESET}")

    all_edges: list[dict] = []
    for event in events:
        all_edges.extend(find_edges(event))

    if not all_edges:
        if meta.get("error") == "missing_api_key":
            p(f"  {YELLOW}Odds feed not configured — set ODDS_API_KEY to enable.{RESET}")
        else:
            p(f"  {GRAY}No games with NY-book odds for this selection.{RESET}")
        p()
        _render_footer(_out, team_filter=bool(team_abv))
        return buf.getvalue()

    if team_abv:
        selected = sorted(all_edges, key=lambda e: -e["ev"])
    else:
        selected = sorted(all_edges, key=lambda e: -e["ev"])[:TOP_N]

    meaningful = [e for e in selected if e["ev"] >= MIN_DISPLAY_EV]

    if not meaningful:
        best = max(all_edges, key=lambda e: e["ev"])
        best_label = _edge_label(best).strip() + f" {odds._fmt_american(best['price'])}"
        p(f"  {GRAY}No meaningful edges today "
          f"(best: {best['ev']*100:+.1f}% EV on {best_label}).{RESET}")
        p()
        _render_footer(_out, team_filter=bool(team_abv))
        return buf.getvalue()

    grouped: dict[str, list[dict]] = {}
    order: list[str] = []
    for edge in meaningful:
        eid = edge["event_id"]
        if eid not in grouped:
            grouped[eid] = []
            order.append(eid)
        grouped[eid].append(edge)

    for eid in order:
        _render_game_block(eid, grouped[eid], _out, tz)

    _render_footer(_out, team_filter=bool(team_abv))
    return buf.getvalue()


def _render_footer(out, team_filter: bool):
    def p(s=""):
        print(s, file=out)
    if not team_filter:
        p(f"  {GRAY}Top {TOP_N} edges, sorted by EV%. {GREEN}Green{RESET}{GRAY} = "
          f"≥{STRONG_EDGE_EV*100:.0f}%. {YELLOW}Yellow{RESET}{GRAY} = "
          f"≥{SUSPICIOUS_EV*100:.0f}% (often stale line — check for late news).{RESET}")
    p(f"  {GRAY}Fair price is the no-vig consensus across NY books. "
      f"Sharp books (Pinnacle/Circa) not included.{RESET}")
    p(f"  {GRAY}Odds via the-odds-api.com. Problem gambling? Call 1-800-GAMBLER.{RESET}")


# ── JSON ──────────────────────────────────────────────────────────────────────

def build_edge_json(edge: dict) -> dict:
    return {
        "market":     edge["market"],
        "outcome":    edge["outcome"],
        "away":       edge["away_abv"],
        "home":       edge["home_abv"],
        "commence":   edge["commence"],
        "price":      edge["price"],
        "point":      edge["point"],
        "book":       edge["book"],
        "book_name":  odds.NY_BOOKS.get(edge["book"], edge["book"]),
        "fair_price": edge["fair_price"],
        "fair_prob":  round(edge["fair_prob"], 4),
        "ev_percent": round(edge["ev"] * 100, 2),
    }
