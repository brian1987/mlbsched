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
curl mlbsched.run/NYY

# Full schedule on a specific date
curl mlbsched.run/2026-04-20

# Team on a specific date
curl mlbsched.run/NYY/2026-04-20

# Tomorrow's schedule
curl mlbsched.run/tomorrow

# Tomorrow for a specific team
curl mlbsched.run/tomorrow/NYY

# Division standings
curl mlbsched.run/standings

# All team abbreviations
curl mlbsched.run/teams
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
