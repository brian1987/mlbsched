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

# Live scores (auto-refresh in browser)
curl mlbsched.run/live

# Boxscore for a team's last/specific game
curl mlbsched.run/box/NYM
curl mlbsched.run/box/NYM/2026-04-20

# Division standings (W-L, PCT, GB, last-10, run differential)
curl mlbsched.run/standings

# Wild Card race per league (3 division leaders + WC1–3 above the cutoff)
curl mlbsched.run/wildcard

# Head-to-head season series between two teams
curl mlbsched.run/h2h/NYM/PHI

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

# All team abbreviations
curl mlbsched.run/teams
```

## Stat aliases for `/leaders/<stat>`

**Hitting:** `avg`, `obp`, `slg`, `ops`, `hr`, `rbi`, `r`, `h`, `2b`, `3b`, `sb`, `bb`, `so`, `tb`
**Pitching:** `era`, `w`, `sv`, `k`, `whip`, `ip`, `kbb`, `hld`, `oba`

## JSON API

Every endpoint above has a JSON variant under `/api/`:

```bash
curl mlbsched.run/api/NYM
curl mlbsched.run/api/wildcard
curl mlbsched.run/api/h2h/NYM/PHI
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
```

## Team Abbreviations

| | | | | | |
|---|---|---|---|---|---|
| ARI | ATL | BAL | BOS | CHC | CIN |
| CLE | COL | CWS | DET | HOU | KC |
| LAA | LAD | MIA | MIL | MIN | NYM |
| NYY | OAK | PHI | PIT | SD | SEA |
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
