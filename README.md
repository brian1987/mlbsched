# mlbsched.run

MLB schedule in your terminal. Inspired by [wttr.in](https://wttr.in).

```
curl mlbsched.run
```

## Usage

```bash
# Today's full schedule
curl mlbsched.run

# Team's game today
curl mlbsched.run/NYM

# Full schedule on a specific date
curl mlbsched.run/2026-04-20

# Team on a specific date
curl mlbsched.run/NYM/2026-04-20

# Tomorrow / yesterday
curl mlbsched.run/tomorrow
curl mlbsched.run/tomorrow/NYM
curl mlbsched.run/yesterday
curl mlbsched.run/yesterday/NYM

# A team's next game (today's if it hasn't finished), with a countdown to first pitch
curl mlbsched.run/NYM/next

# Live scores (auto-refresh in browser)
curl mlbsched.run/live

# Boxscore for a team's last/specific game (or a random one from team history)
curl mlbsched.run/box/NYM
curl mlbsched.run/box/NYM/2026-04-20
curl mlbsched.run/box/NYM/random

# Division standings (W-L, PCT, GB, last-10, run differential, magic number)
curl mlbsched.run/standings

# Wild Card race per league (3 division leaders + WC1–3 above the cutoff)
curl mlbsched.run/wildcard

# Playoff bracket — series records, live games, what's next (or a past year)
curl mlbsched.run/postseason
curl mlbsched.run/postseason/2015

# Head-to-head season series between two teams
curl mlbsched.run/h2h/NYM/PHI

# Today's probable starting-pitcher matchups (season W-L, ERA, WHIP)
curl mlbsched.run/pitchers

# Today's batting order for a team's game (~3 hrs before first pitch)
curl mlbsched.run/lineup/NYM

# Player season stats + last game (substring match on name)
curl mlbsched.run/player/judge
curl mlbsched.run/player/ohtani

# Stat leaders dashboard (HR, AVG, OPS, W, ERA, K)
curl mlbsched.run/leaders

# Top 25 in a single stat — hitting or pitching
curl mlbsched.run/leaders/ops
curl mlbsched.run/leaders/era
curl mlbsched.run/leaders/whip

# Hot / cold streaks (4+ games; ?min=N to override)
curl mlbsched.run/streaks

# Today's odds — best NY sportsbook price per market
curl mlbsched.run/odds
curl mlbsched.run/odds/NYM

# Pricing edges via multi-book no-vig consensus
curl mlbsched.run/bestbets

# Weather at every stadium with a game today
curl mlbsched.run/weather

# Today's games sorted by distance from your IP
curl mlbsched.run/distance

# TV broadcasts for today's games (English, national + home/away local feeds)
curl mlbsched.run/broadcasts
curl mlbsched.run/broadcasts/NYM

# Random mascot ASCII art
curl mlbsched.run/random

# Today's schedule (minimal scoreboard, no live highlight section)
curl mlbsched.run/today

# On this date in MLB history — games from 10, 25, and 50 years ago
curl mlbsched.run/onthisday

# Active players born on today's date
curl mlbsched.run/birthdays

# All-time legends born on today's date (ranked by seasons played)
curl mlbsched.run/birthdays/all

# Win-probability sparkline for a team's completed game(s) on a date (defaults to yesterday)
curl mlbsched.run/wp/NYM
curl mlbsched.run/wp/NYM/2025-10-04

# All team abbreviations
curl mlbsched.run/teams
```

## Admin

```bash
# Request metrics (last N days, default 30, max 365)
# Requires MLBSCHED_METRICS_TOKEN env on the server (set via `fly secrets set`)
curl -H "Authorization: Bearer $MLBSCHED_METRICS_TOKEN" mlbsched.run/metrics
curl -H "Authorization: Bearer $MLBSCHED_METRICS_TOKEN" mlbsched.run/metrics?days=7
```

## Stat aliases for `/leaders/<stat>`

**Hitting:** `avg`, `obp`, `slg`, `ops`, `hr`, `rbi`, `r`, `h`, `2b`, `3b`, `sb`, `bb`, `so`, `tb`
**Pitching:** `era`, `w`, `sv`, `k`, `whip`, `ip`, `kbb`, `hld`, `oba`

## Magic and elimination numbers

From midseason on, `/standings` and `/wildcard` carry the pennant race:

| Column | Meaning |
|---|---|
| `M#` | Magic number — wins by this team plus losses by its closest chaser that clinch the division |
| `E#` | Elimination number — games until the team is eliminated (`/wildcard` shows the wild-card version) |
| `x` | Clinched a playoff berth |
| `w` | Clinched a wild card |
| `y` | Clinched the division |
| `z` | Clinched the best record in the league |

A `-` means the number doesn't apply — a trailing team has no magic number, a
division leader has no elimination number. Both come straight from the MLB Stats
API, and the columns are omitted entirely in the early season and the offseason,
when MLB isn't publishing them.

## Postseason

From the day the bracket is published, schedule lines carry a series tag
(`ALWC G1`, `NLDS G2`, `ALCS G5`, `WS G7`). Slots MLB hasn't filled yet show
`TBD` with the slot's name beneath ("AL Wild Card #2 @ New York Yankees"), and
a game whose first pitch hasn't been set prints `TBD` instead of a time.

## JSON API

Every data endpoint above has a JSON variant under `/api/`:

```bash
curl mlbsched.run/api/NYM
curl mlbsched.run/api/NYM/next
curl mlbsched.run/api/NYM/2026-04-20
curl mlbsched.run/api/2026-04-20
curl mlbsched.run/api/yesterday
curl mlbsched.run/api/yesterday/NYM
curl mlbsched.run/api/tomorrow
curl mlbsched.run/api/tomorrow/NYM
curl mlbsched.run/api/box/NYM
curl mlbsched.run/api/box/NYM/2026-04-20
curl mlbsched.run/api/box/NYM/random
curl mlbsched.run/api/standings
curl mlbsched.run/api/teams
curl mlbsched.run/api/wildcard
curl mlbsched.run/api/postseason
curl mlbsched.run/api/postseason/2015
curl mlbsched.run/api/h2h/NYM/PHI
curl mlbsched.run/api/pitchers
curl mlbsched.run/api/lineup/NYM
curl mlbsched.run/api/player/judge
curl mlbsched.run/api/leaders
curl mlbsched.run/api/leaders/ops
curl mlbsched.run/api/streaks
curl mlbsched.run/api/odds
curl mlbsched.run/api/bestbets
curl mlbsched.run/api/weather
curl mlbsched.run/api/distance
curl mlbsched.run/api/live
curl mlbsched.run/api/random
curl mlbsched.run/api/broadcasts
curl mlbsched.run/api/broadcasts/NYM
curl mlbsched.run/api/today
curl mlbsched.run/api/onthisday
curl mlbsched.run/api/birthdays
curl mlbsched.run/api/birthdays/all
curl mlbsched.run/api/wp/NYM
curl mlbsched.run/api/wp/NYM/2025-10-04
```

### Status codes

Errors carry a real status code on both the text and JSON routes, so `curl -f` and
`resp.raise_for_status()` do the right thing:

| Code | Meaning |
|---|---|
| `200` | OK |
| `400` | Well-formed but nonsensical (`/h2h/NYM/NYM`) |
| `404` | Unknown team, stat, player, or date (`/lineup/ZZZ`, `/leaders/bogus`) |
| `422` | Bad query parameter (`/streaks?min=abc`) |
| `503` | An upstream data source is down — retry after 30s |

```bash
# Fails loudly instead of piping an error page into your script
curl -fsS mlbsched.run/box/NYM || echo "no game"
```

## Team Abbreviations

| | | | | | |
|---|---|---|---|---|---|
| ARI | ATH | ATL | BAL | BOS | CHC |
| CIN | CLE | COL | CWS | DET | HOU |
| KC | LAA | LAD | MIA | MIL | MIN |
| NYM | NYY | PHI | PIT | SD | SEA |
| SF | STL | TB | TEX | TOR | WSH |

## Run Locally

```bash
git clone https://github.com/brian1987/mlbsched.git
cd mlbsched
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# CLI
python mlbsched.py
python mlbsched.py NYY
python mlbsched.py standings
python mlbsched.py wildcard

# Web server
uvicorn server:app --port 8080
curl http://localhost:8080/NYY
```

## Deploy Your Own

Requires [flyctl](https://fly.io/docs/hands-on/install-flyctl/).

```bash
fly auth login
fly launch
fly deploy
```

## Stack

- **Data**: [MLB Stats API](https://statsapi.mlb.com) (free, no auth required)
- **Server**: [FastAPI](https://fastapi.tiangolo.com) + [uvicorn](https://www.uvicorn.org)
- **Hosting**: [fly.io](https://fly.io)
- **Domain**: [mlbsched.run](http://mlbsched.run)
