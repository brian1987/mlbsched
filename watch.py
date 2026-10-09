"""Watch a game live in the terminal: /watch/<TEAM>.

`curl mlbsched.run/watch/CLE` holds the connection open and redraws one screen in
place every few seconds: line score, the diamond with runners on, balls/strikes/
outs, who's batting and who's pitching (with a pitch count), the last pitch and
the last play. The stream ends itself after the final out. Browsers get the same
frame on a short meta refresh; /api/watch/<TEAM> serves the snapshot as JSON.

Every viewer of a game shares one upstream poll: the MLB live feed is cached per
game for a few seconds behind a lock, so a crowd costs what one viewer does.

/replay/<gamePk> plays a finished game back pitch by pitch through the same
screen, using the feed's timecodes (MLB keeps a snapshot of every update).
"""

import asyncio
import io
import textwrap
import threading
import time
from datetime import datetime, timedelta, timezone as _UTC
from zoneinfo import ZoneInfo

import requests

import mlbsched as sched
from mlbsched import BOLD, DIM, RESET, RED, GREEN, YELLOW, CYAN, WHITE, GRAY

FEED_URL = "https://statsapi.mlb.com/api/v1.1/game/{pk}/feed/live"

# The full feed runs to ~1 MB by the late innings. StatsAPI's `fields` filter keeps
# keys by name at any depth, which trims it to what this screen reads (~75-200 KB).
FEED_FIELDS = ",".join([
    "metaData", "timeStamp",
    "gameData", "game", "pk", "type", "status", "abstractGameState", "detailedState",
    "statusCode", "startTimeTBD", "reason", "teams", "away", "home", "id", "name",
    "datetime", "dateTime", "officialDate", "venue", "probablePitchers", "fullName",
    "liveData", "linescore", "currentInning", "currentInningOrdinal", "inningState",
    "inningHalf", "isTopInning", "scheduledInnings", "innings", "num", "runs", "hits",
    "errors", "offense", "defense", "batter", "pitcher", "first", "second", "third",
    "balls", "strikes", "outs", "plays", "allPlays", "result", "event", "description",
    "about", "halfInning", "inning", "isComplete", "isScoringPlay", "matchup",
    "batSide", "pitchHand", "code", "playEvents", "details", "isPitch", "pitchData",
    "startSpeed", "decisions", "winner", "loser", "save",
])

# How long a cached feed stays fresh, and how often a viewer's screen redraws,
# by game state. Live is ~one redraw per pitch; a pre-game screen only has a
# countdown to move.
_FEED_TTL  = {"Live": 4, "Preview": 20, "Final": 300}
_INTERVAL  = {"Live": 5, "Preview": 20}
_DELAYED_INTERVAL = 30

MAX_STREAMS    = 400                    # concurrent /watch streams before falling back to a snapshot
MAX_SECONDS    = 6 * 60 * 60            # one stream's lifetime: pre-game + a long extra-inning game
PREGAME_WINDOW = timedelta(hours=3)     # stream a countdown only this close to first pitch
REPLAY_STEP_SECONDS = 1.5               # one feed update per frame in /replay

WIDTH = 58

# Terminal control: clear once, then repaint from the top-left each frame,
# erasing each line's tail and everything below so a shorter frame leaves no debris.
CLEAR = "\x1b[2J\x1b[H"
HOME  = "\x1b[H"
EOL   = "\x1b[K"
EOS   = "\x1b[J"


# ── Feed fetch: shared per-game cache + serve-stale-on-error ───────────────────
_feed_cache: dict[int, tuple[float, dict]] = {}     # gamePk -> (fetched_at_monotonic, feed)
_feed_locks: dict[int, asyncio.Lock] = {}
_sync_lock = threading.Lock()


def _state(feed: dict) -> str:
    return ((feed.get("gameData") or {}).get("status") or {}).get("abstractGameState", "Preview")


def _fresh(entry: tuple[float, dict] | None, now: float) -> bool:
    return entry is not None and now - entry[0] < _FEED_TTL.get(_state(entry[1]), 5)


def _get_feed(pk: int, timecode: str | None = None) -> dict:
    params = {"fields": FEED_FIELDS}
    if timecode:
        params["timecode"] = timecode
    resp = requests.get(FEED_URL.format(pk=pk), params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()


def fetch_feed(pk: int) -> dict:
    """The game's live feed, at most a few seconds old. Serves the last good copy
    if MLB hiccups mid-game rather than blanking every viewer's screen."""
    now = time.monotonic()
    cached = _feed_cache.get(pk)
    if _fresh(cached, now):
        return cached[1]
    try:
        feed = _get_feed(pk)
    except requests.RequestException:
        if cached is not None:
            return cached[1]
        raise
    with _sync_lock:
        if len(_feed_cache) > 64:          # games from earlier days nobody's watching
            for k, (t, _) in list(_feed_cache.items()):
                if now - t > 3600:
                    del _feed_cache[k]
        _feed_cache[pk] = (now, feed)
    return feed


async def fetch_feed_async(pk: int) -> dict:
    """fetch_feed for the streaming loop: a fresh cache hit never leaves the event
    loop, and only one viewer per game goes upstream when it's stale."""
    if _fresh(_feed_cache.get(pk), time.monotonic()):
        return _feed_cache[pk][1]
    lock = _feed_locks.setdefault(pk, asyncio.Lock())
    async with lock:
        return await asyncio.to_thread(fetch_feed, pk)


# ── Which game to watch ────────────────────────────────────────────────────────
def find_game(team_abv: str) -> dict | None:
    """The schedule entry for the team's game worth watching: one in progress,
    else today's next one, else today's last final, else the next on the calendar."""
    team_id = sched.TEAMS[team_abv][0]

    def mine(g: dict) -> bool:
        return team_id in (g["teams"]["away"]["team"].get("id"), g["teams"]["home"]["team"].get("id"))

    live = [g for g in sched.live_games_now() if mine(g)]
    if live:
        return live[0]
    data = sched.fetch_schedule(sched.today_et().strftime("%Y-%m-%d"), team_id)
    today = [g for block in data.get("dates", []) for g in block.get("games", [])]
    upcoming = [g for g in today
                if g["status"]["abstractGameState"] != "Final" and not sched._is_no_play(g)]
    if upcoming:
        return upcoming[0]
    finals = [g for g in today if g["status"]["abstractGameState"] == "Final"]
    if finals:
        return finals[-1]
    return sched.fetch_next_game(team_id)


def find_game_by_pk(pk: int) -> dict | None:
    """Schedule entry for one gamePk (for the series tag), or None."""
    try:
        resp = requests.get(f"{sched.MLB_API}/schedule",
                            params={"sportId": 1, "gamePk": pk, "hydrate": "team"}, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException:
        return None
    return next((g for block in data.get("dates", []) for g in block.get("games", [])), None)


# ── Snapshot: the feed boiled down to what the screen (and /api/watch) shows ───
def _name(person: dict | None) -> str | None:
    return (person or {}).get("fullName") or None


def snapshot(feed: dict, game: dict | None = None) -> dict:
    gd     = feed.get("gameData") or {}
    ld     = feed.get("liveData") or {}
    ls     = ld.get("linescore") or {}
    plays  = (ld.get("plays") or {}).get("allPlays") or []
    status = gd.get("status") or {}
    teams  = gd.get("teams") or {}
    totals = ls.get("teams") or {}
    offense = ls.get("offense") or {}
    defense = ls.get("defense") or {}

    def side(key: str) -> dict:
        t = teams.get(key) or {}
        tot = totals.get(key) or {}
        return {
            "abv":    sched.team_label(t) if t else "TBD",
            "name":   t.get("name") or "",
            "runs":   tot.get("runs"),
            "hits":   tot.get("hits"),
            "errors": tot.get("errors"),
        }

    pitcher = defense.get("pitcher") or {}
    batter  = offense.get("batter") or {}
    last    = plays[-1] if plays else {}
    matchup = last.get("matchup") or {}

    def hand(person_key: str, hand_key: str, person: dict) -> str | None:
        m = matchup.get(person_key) or {}
        if person.get("id") and m.get("id") == person.get("id"):
            return (matchup.get(hand_key) or {}).get("code")
        return None

    pitch_count = None
    if pitcher.get("id"):
        pitch_count = sum(
            1
            for p in plays
            if ((p.get("matchup") or {}).get("pitcher") or {}).get("id") == pitcher["id"]
            for e in p.get("playEvents") or []
            if e.get("isPitch")
        )

    # Only the latest plate appearance's pitch: once a new batter steps in, the
    # previous one's last pitch would contradict the fresh 0-0 count.
    last_pitch = None
    ev = next((e for e in reversed(last.get("playEvents") or []) if e.get("isPitch")), None)
    if ev:
        d = ev.get("details") or {}
        last_pitch = {
            "call": d.get("description"),
            "type": (d.get("type") or {}).get("description"),
            "mph":  (ev.get("pitchData") or {}).get("startSpeed"),
        }

    last_play = None
    for p in reversed(plays):
        about = p.get("about") or {}
        res = p.get("result") or {}
        if about.get("isComplete") and res.get("description"):
            last_play = {
                "text":    res["description"],
                "event":   res.get("event"),
                "scoring": bool(about.get("isScoringPlay")),
                "inning":  about.get("inning"),
                "half":    about.get("halfInning"),
            }
            break

    # A mound visit, pitching change or stolen base lands as a non-pitch event at
    # the end of the current play; show it when it's the very latest thing.
    note = None
    events = last.get("playEvents") or []
    if events and not events[-1].get("isPitch"):
        note = (events[-1].get("details") or {}).get("description")

    decisions = ld.get("decisions") or {}
    probables = gd.get("probablePitchers") or {}
    dt = gd.get("datetime") or {}

    return {
        "gamePk":     (gd.get("game") or {}).get("pk") or feed.get("gamePk"),
        "status":     status.get("abstractGameState", "Preview"),
        "detailed":   status.get("detailedState", ""),
        "reason":     status.get("reason"),
        "start":      dt.get("dateTime"),
        "start_tbd":  bool(status.get("startTimeTBD")),
        "date":       dt.get("officialDate"),
        "tag":        sched.game_tag(game) if game else "",
        "venue":      (gd.get("venue") or {}).get("name"),
        "away":       side("away"),
        "home":       side("home"),
        "inning":     ls.get("currentInning"),
        "inning_ordinal": ls.get("currentInningOrdinal"),
        "inning_state":   ls.get("inningState"),          # Top / Middle / Bottom / End
        "scheduled_innings": ls.get("scheduledInnings") or 9,
        "balls":      ls.get("balls"),
        "strikes":    ls.get("strikes"),
        "outs":       ls.get("outs"),
        "runners":    {b: _name(offense.get(b)) for b in ("first", "second", "third")},
        "batter":     {"name": _name(batter), "bats": hand("batter", "batSide", batter)} if batter else None,
        "pitcher":    {"name": _name(pitcher), "throws": hand("pitcher", "pitchHand", pitcher),
                       "pitches": pitch_count} if pitcher else None,
        "innings":    [{"num": i.get("num"),
                        "away": (i.get("away") or {}).get("runs"),
                        "home": (i.get("home") or {}).get("runs")} for i in ls.get("innings") or []],
        "last_pitch": last_pitch,
        "last_play":  last_play,
        "note":       note,
        "probables":  {"away": _name(probables.get("away")), "home": _name(probables.get("home"))},
        "decisions":  {k: _name(decisions.get(k)) for k in ("winner", "loser", "save")},
        "as_of":      (feed.get("metaData") or {}).get("timeStamp"),
    }


def is_over(snap: dict) -> bool:
    d = (snap.get("detailed") or "").lower()
    return snap["status"] == "Final" or any(w in d for w in ("postponed", "cancel", "suspended"))


def is_delayed(snap: dict) -> bool:
    return "delay" in (snap.get("detailed") or "").lower()


def in_play(snap: dict) -> bool:
    """A half-inning is underway (not between halves, not before or after the game)."""
    return snap["status"] == "Live" and snap.get("inning_state") in ("Top", "Bottom") and not is_delayed(snap)


def interval(snap: dict) -> float:
    if is_delayed(snap):
        return _DELAYED_INTERVAL
    return _INTERVAL.get(snap["status"], 20)


def worth_streaming(snap: dict, now: datetime | None = None) -> bool:
    """Stream if the game is on, or close enough to first pitch that a countdown
    is worth leaving open. Further out, one frame and a 'come back later'."""
    if is_over(snap):
        return False
    if snap["status"] == "Live":
        return True
    try:
        start = datetime.strptime(snap.get("start") or "", "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=_UTC.utc)
    except ValueError:
        return False
    return start - (now or datetime.now(_UTC.utc)) <= PREGAME_WINDOW


# ── Rendering ──────────────────────────────────────────────────────────────────
def _score(v: int | None) -> str:
    return " -" if v is None else f"{v:>2}"


def _state_label(snap: dict) -> str:
    if is_over(snap):
        if snap["status"] == "Final":
            n = len(snap["innings"])
            extra = f"/{n}" if n and n != snap["scheduled_innings"] else ""
            return f"{BOLD}{WHITE}Final{extra}{RESET}"
        return f"{BOLD}{YELLOW}{snap['detailed']}{RESET}"
    if is_delayed(snap):
        reason = f": {snap['reason']}" if snap.get("reason") and snap["reason"] not in snap["detailed"] else ""
        return f"{BOLD}{YELLOW}{snap['detailed']}{reason}{RESET}"
    if snap["status"] == "Live":
        st, ordn = snap.get("inning_state") or "", snap.get("inning_ordinal") or ""
        if st == "Top":
            return f"{BOLD}{GREEN}▲ {ordn}{RESET}"
        if st == "Bottom":
            return f"{BOLD}{GREEN}▼ {ordn}{RESET}"
        if st in ("Middle", "End"):
            return f"{GREEN}{st} {ordn}{RESET}"
        return f"{GREEN}{snap['detailed']}{RESET}"
    return f"{CYAN}{snap['detailed'] or 'Scheduled'}{RESET}"


def _header(snap: dict) -> str:
    a, h = snap["away"], snap["home"]
    started = snap["status"] != "Preview"

    def score(team: dict, other: dict) -> str:
        if not started:
            return "  "
        lead = team["runs"] is not None and other["runs"] is not None and team["runs"] > other["runs"]
        return f"{BOLD}{WHITE if lead else GRAY}{_score(team['runs'])}{RESET}"

    tag = f"   {GRAY}{snap['tag']}{RESET}" if snap.get("tag") else ""
    return (f"  {sched.fmt_team(a['abv'])} {score(a, h)}  {DIM}@{RESET}  "
            f"{sched.fmt_team(h['abv'])} {score(h, a)}     {_state_label(snap)}{tag}")


def _linescore(snap: dict) -> list[str]:
    innings = snap["innings"]
    n = max(snap["scheduled_innings"], len(innings))
    final = snap["status"] == "Final"
    live_half = None
    if in_play(snap):
        live_half = (snap["inning"], "away" if snap["inning_state"] == "Top" else "home")

    head = "".join(f"{i:>3}" for i in range(1, n + 1))
    lines = [f"  {GRAY}     {head}     R  H  E{RESET}"]
    for key in ("away", "home"):
        t = snap[key]
        cells = []
        for i in range(1, n + 1):
            inn = innings[i - 1] if i <= len(innings) else None
            runs = inn.get(key) if inn else None
            if runs is None:
                # The home half of a final inning that was never needed reads "x".
                cell = " x" if final and key == "home" and inn is not None else "  "
                cells.append(f" {GRAY}{cell}{RESET}")
            elif live_half == (i, key):
                cells.append(f" {BOLD}{GREEN}{runs:>2}{RESET}")
            else:
                cells.append(f" {WHITE if runs else GRAY}{runs:>2}{RESET}")
        r, hi, e = (t[k] for k in ("runs", "hits", "errors"))
        lines.append(
            f"  {sched.fmt_team(t['abv'])}  {''.join(cells)}   "
            f"{BOLD}{WHITE}{_score(r)}{RESET} {GRAY}{_score(hi)} {_score(e)}{RESET}"
        )
    return lines


def _diamond(snap: dict) -> list[str]:
    on = snap["runners"]

    def base(b: str) -> str:
        return f"{BOLD}{YELLOW}◆{RESET}" if on.get(b) else f"{GRAY}◇{RESET}"

    def dots(n: int | None, total: int, color: str) -> str:
        n = max(0, min(n or 0, total))
        return f"{color}{'● ' * n}{RESET}{GRAY}{'○ ' * (total - n)}{RESET}"

    if in_play(snap):
        count = [
            f"{GRAY}B{RESET} {dots(snap['balls'], 3, GREEN)}",
            f"{GRAY}S{RESET} {dots(snap['strikes'], 2, YELLOW)}",
            f"{GRAY}O{RESET} {dots(snap['outs'], 2, RED)}",
        ]
    else:
        count = ["", "", ""]
    return [
        f"          {base('second')}              {count[0]}",
        f"        {GRAY}╱   ╲{RESET}            {count[1]}",
        f"      {base('third')}       {base('first')}          {count[2]}",
        f"        {GRAY}╲   ╱{RESET}",
        f"          {GRAY}⌂{RESET}",
    ]


def _wrap(text: str, first: str, rest: str, color: str = WHITE, max_lines: int = 3) -> list[str]:
    lines = textwrap.wrap(text, width=WIDTH - 4)[:max_lines]
    return [f"{first if i == 0 else rest}{color}{l}{RESET}" for i, l in enumerate(lines)]


def _when(iso: str | None, tz: ZoneInfo | None) -> str:
    return sched.fmt_game_time(iso or "", tz)


def render_frame(snap: dict, tz: ZoneInfo | None = None, mode: str = "stream",
                 now: datetime | None = None) -> str:
    """One screen. mode: 'stream' (curl, redrawn in place), 'once' (a single
    snapshot), 'browser' (meta refresh), 'replay' (as_of is the game clock)."""
    now = now or datetime.now(_UTC.utc)
    buf = io.StringIO()

    def p(s: str = "") -> None:
        print(s, file=buf)

    p()
    p(_header(snap))
    p(f"  {GRAY}{'─' * WIDTH}{RESET}")

    if snap["status"] == "Preview" and not is_over(snap):
        when = "TBD" if snap["start_tbd"] else _when(snap["start"], tz)
        cd = "" if snap["start_tbd"] else sched.countdown_label(snap.get("start") or "", now)
        day = sched.day_label(snap.get("date") or "")
        p(f"  {GRAY}First pitch{RESET}  {CYAN}{day + ' ' if day and day != 'Today' else ''}{when}{RESET}"
          f"{f'  {GRAY}({cd}){RESET}' if cd else ''}")
        if snap.get("venue"):
            p(f"  {GRAY}{snap['venue']}{RESET}")
        pr = snap["probables"]
        if pr["away"] or pr["home"]:
            p()
            p(f"  {GRAY}Probable starters{RESET}")
            p(f"    {sched.fmt_team(snap['away']['abv'])}  {pr['away'] or 'TBD'}")
            p(f"    {sched.fmt_team(snap['home']['abv'])}  {pr['home'] or 'TBD'}")
    else:
        for line in _linescore(snap):
            p(line)
        if not is_over(snap):
            p()
            for line in _diamond(snap):
                p(line)
            if in_play(snap):
                p()
                b, pi = snap["batter"] or {}, snap["pitcher"] or {}
                if b.get("name"):
                    bats = f" {GRAY}({b['bats']}){RESET}" if b.get("bats") else ""
                    p(f"  {GRAY}AB{RESET}  {WHITE}{b['name']}{RESET}{bats}")
                if pi.get("name"):
                    throws = f" {GRAY}({pi['throws']}){RESET}" if pi.get("throws") else ""
                    count = f"  {GRAY}· {pi['pitches']} pitches{RESET}" if pi.get("pitches") else ""
                    p(f"  {GRAY}P{RESET}   {WHITE}{pi['name']}{RESET}{throws}{count}")
                lp = snap["last_pitch"]
                if lp and lp.get("call"):
                    mph = f"{lp['mph']:.1f} mph " if lp.get("mph") else ""
                    kind = f"{lp['type']} " if lp.get("type") else ""
                    p(f"  {GRAY}▸{RESET} {CYAN}{mph}{kind}{RESET}{GRAY}—{RESET} {lp['call']}")
        else:
            d = snap["decisions"]
            bits = [f"{GRAY}{k}{RESET} {v}" for k, v in (("W", d["winner"]), ("L", d["loser"]), ("S", d["save"])) if v]
            if bits:
                p()
                p("  " + "   ".join(bits))
        play = snap["last_play"]
        if play:
            p()
            color = f"{BOLD}{YELLOW}" if play["scoring"] else WHITE
            # Say which half it came from unless it's the half being played now —
            # the third out of the last half, shown under a fresh 0-out count, reads wrong otherwise.
            text = play["text"]
            # Between halves, the half that just ended is the current one ("Middle" follows the top).
            state = {"middle": "top", "end": "bottom"}.get((snap.get("inning_state") or "").lower(),
                                                           (snap.get("inning_state") or "").lower())
            current = (snap.get("inning"), state)
            if play.get("inning") and (play["inning"], play.get("half")) != current and not is_over(snap):
                text = f"{'Top' if play.get('half') == 'top' else 'Bot'} {play['inning']}: {text}"
            for line in _wrap(text, f"  {GRAY}»{RESET} ", "    ", color):
                p(line)
        if snap.get("note") and not is_over(snap):
            for line in _wrap(snap["note"], f"  {GRAY}»{RESET} ", "    ", GRAY, max_lines=2):
                p(line)

    p()
    p(_footer(snap, tz, mode, now))
    return buf.getvalue()


def _footer(snap: dict, tz: ZoneInfo | None, mode: str, now: datetime) -> str:
    venue = f"{snap['venue']} · " if snap.get("venue") and snap["status"] != "Preview" else ""
    if mode == "replay":
        clock = ""
        try:
            t = datetime.strptime(snap.get("as_of") or "", "%Y%m%d_%H%M%S").replace(tzinfo=_UTC.utc)
            clock = t.astimezone(tz or sched.ET).strftime("%b %-d, %-I:%M:%S %p %Z")
        except ValueError:
            pass
        return f"  {GRAY}{venue}{RESET}{YELLOW}REPLAY{RESET}{GRAY} {clock} · ctrl-c to quit{RESET}"
    if is_over(snap):
        home = snap["home"]["abv"]
        box = f"curl mlbsched.run/box/{home}/{snap['date']}" if snap.get("date") else f"curl mlbsched.run/box/{home}"
        return f"  {GRAY}{venue}box score: {box}{RESET}"
    clock = now.astimezone(tz or sched.ET).strftime("%-I:%M:%S %p %Z")
    tail = {"stream": " · ctrl-c to quit", "browser": " · refreshes every 10s"}.get(mode, "")
    return f"  {GRAY}{venue}updated {clock}{tail}{RESET}"


def paint(frame: str) -> str:
    """A frame as terminal output that overwrites the previous one in place."""
    return HOME + "".join(f"{line}{EOL}\n" for line in frame.rstrip("\n").split("\n")) + EOS


def render_index(tz: ZoneInfo | None = None) -> str:
    """/watch with no team: today's games, each with the command that watches it."""
    buf = io.StringIO()

    def p(s: str = "") -> None:
        print(s, file=buf)

    today = sched.today_et()
    seen: set[int] = set()
    games = []
    for g in sched.live_games_now() + [
        g for block in sched.fetch_schedule(today.strftime("%Y-%m-%d")).get("dates", [])
        for g in block.get("games", [])
    ]:
        if g["gamePk"] not in seen:
            seen.add(g["gamePk"])
            games.append(g)

    p()
    p(f"  {BOLD}{GREEN}Watch Live{RESET} — {BOLD}{WHITE}{today.strftime('%A, %B %-d, %Y')}{RESET}")
    p(f"  {GRAY}{'─' * 52}{RESET}")
    if not games:
        p(f"  {GRAY}No games today.{RESET}")
    for g in games:
        sched._render_game_line(g, buf, tz=tz)
        home = g["teams"]["home"]["team"]
        if g["status"]["abstractGameState"] != "Final" and not sched.is_placeholder_team(home):
            p(f"         {GRAY}curl mlbsched.run/watch/{sched.team_label(home)}{RESET}")
    p()
    p(f"  {GRAY}Either team's abbreviation works. The screen redraws in place until the final out.{RESET}")
    p()
    return buf.getvalue()


# ── Streaming ──────────────────────────────────────────────────────────────────
_active = 0


def active_streams() -> int:
    return _active


async def stream(game: dict, tz: ZoneInfo | None, is_disconnected):
    """Redraw the game every few seconds until it's over, the viewer leaves, or
    the session cap. Yields terminal output for a StreamingResponse."""
    global _active
    _active += 1
    pk = game["gamePk"]
    started = time.monotonic()
    try:
        yield CLEAR
        while True:
            snap = snapshot(await fetch_feed_async(pk), game)
            yield paint(render_frame(snap, tz=tz, mode="stream"))
            if is_over(snap):
                break
            if not worth_streaming(snap):
                cd = sched.countdown_label(snap.get("start") or "")
                yield (f"  {GRAY}First pitch is {cd or 'a while off'}. This screen streams from "
                       f"{int(PREGAME_WINDOW.total_seconds() // 3600)} hours before — come back then.{RESET}\n")
                break
            if time.monotonic() - started > MAX_SECONDS:
                yield f"  {GRAY}Session limit reached — run the command again to keep watching.{RESET}\n"
                break
            await asyncio.sleep(interval(snap))
            if await is_disconnected():
                break
    finally:
        _active -= 1


# ── Replay ─────────────────────────────────────────────────────────────────────
_stamps_cache: dict[int, list[str]] = {}


def fetch_timestamps(pk: int) -> list[str]:
    """Every timecode MLB snapshotted for a game (one per pitch/play update).
    Cached for good once the game is final; it no longer changes."""
    if pk in _stamps_cache:
        return _stamps_cache[pk]
    resp = requests.get(FEED_URL.format(pk=pk) + "/timestamps", timeout=10)
    resp.raise_for_status()
    stamps = [s for s in resp.json() if isinstance(s, str)]
    if len(_stamps_cache) > 64:
        _stamps_cache.clear()
    _stamps_cache[pk] = stamps
    return stamps


async def replay(pk: int, game: dict | None, tz: ZoneInfo | None, is_disconnected,
                 speed: float = 1.0, start: int = 0):
    """Play a finished game back through the live screen, one feed update per
    frame, from update number `start`."""
    global _active
    _active += 1
    began = time.monotonic()
    step = max(0.2, REPLAY_STEP_SECONDS / max(speed, 0.1))
    try:
        stamps = await asyncio.to_thread(fetch_timestamps, pk)
        yield CLEAR
        for tc in stamps[max(0, start):]:
            try:
                feed = await asyncio.to_thread(_get_feed, pk, tc)
            except requests.RequestException:
                await asyncio.sleep(step)
                continue
            snap = snapshot(feed, game)
            if snap["status"] == "Preview":
                continue                       # skip the pre-game hours
            yield paint(render_frame(snap, tz=tz, mode="replay"))
            if is_over(snap) or time.monotonic() - began > MAX_SECONDS:
                break
            await asyncio.sleep(step)
            if await is_disconnected():
                break
    finally:
        _active -= 1
