#!/usr/bin/env python3
"""Build static/birthdays_alltime.json — the all-time "legends born today" index.

Dev-only tool. NOT a runtime dependency: run it locally and commit the resulting
static/birthdays_alltime.json. The set of historical players born on a given date
never changes, so a re-run is only needed to fold in newly-debuted players (and
even then the live /birthdays "active" mode already covers current players).

    ./venv/bin/python tools/build_birthdays_alltime.py

Why precompute instead of sweeping at request time: a complete answer needs every
season since 1876 (~150 calls, ~1.2 min). Doing that on a cold request would be
slow and would hammer the upstream — exactly the cold-start cost that kept this
feature on the shelf. Building once offline keeps the route instant.
"""

import json
import time
from pathlib import Path

import requests

MLB_API = "https://statsapi.mlb.com/api/v1"
FIRST_SEASON = 1876          # first National League season
STATIC = Path(__file__).resolve().parent.parent / "static"
OUT = STATIC / "birthdays_alltime.json"

# How many players to keep per calendar day. Ranked by career length, so the
# tail we drop is the shortest-career journeymen — never the legends. Generous
# enough that the JSON API still returns a full slate.
KEEP_PER_DAY = 40


def _position(p: dict) -> str:
    pos = (p.get("primaryPosition") or {}).get("abbreviation") or "?"
    if pos == "P":
        hand = (p.get("pitchHand") or {}).get("code")
        return {"L": "LHP", "R": "RHP"}.get(hand, "P")
    return pos


def _year(s: str | None) -> int | None:
    return int(s[:4]) if s and len(s) >= 4 else None


def main() -> None:
    this_year = time.gmtime().tm_year
    seasons = range(FIRST_SEASON, this_year + 1)

    by_id: dict[int, dict] = {}
    start = time.time()
    for season in seasons:
        try:
            resp = requests.get(
                f"{MLB_API}/sports/1/players",
                params={"season": season},
                timeout=20,
            )
            resp.raise_for_status()
            people = resp.json().get("people", [])
        except requests.RequestException as exc:
            print(f"  ! season {season} failed: {exc}")
            continue

        for p in people:
            pid = p.get("id")
            birth = p.get("birthDate")
            if pid is None or not birth or len(birth) < 10:
                continue
            # First sighting wins for static bio fields; a later season only
            # extends the career window (lastPlayedDate), handled below.
            entry = by_id.get(pid)
            if entry is None:
                nick = (p.get("nickName") or "").strip()
                entry = {
                    "name":  p.get("fullName"),
                    "nick":  nick if nick and nick != p.get("fullName") else None,
                    "pos":   _position(p),
                    "birth": birth,
                    "debut": p.get("mlbDebutDate"),
                    "last":  p.get("lastPlayedDate"),
                    "death": p.get("deathDate"),
                    "city":  p.get("birthCity") or "",
                    "region": p.get("birthStateProvince") or p.get("birthCountry") or "",
                    # Career window from the seasons actually swept — robust even
                    # when lastPlayedDate is missing for old-timers.
                    "_first": season,
                    "_last": season,
                    "_n": 1,
                }
                by_id[pid] = entry
            else:
                entry["_last"] = season
                entry["_n"] += 1
                # Extend display dates if this season has fresher endpoints.
                if p.get("lastPlayedDate"):
                    entry["last"] = p["lastPlayedDate"]
                if not entry.get("debut") and p.get("mlbDebutDate"):
                    entry["debut"] = p["mlbDebutDate"]

        print(f"  {season}: {len(people):>5} players  (unique so far: {len(by_id)})")

    # Bucket by MM-DD and rank each day by career span (legends bubble up).
    days: dict[str, list[dict]] = {}
    for entry in by_id.values():
        mmdd = entry["birth"][5:10]
        days.setdefault(mmdd, []).append(entry)

    for mmdd, entries in days.items():
        # Rank by number of seasons in the bigs — our best cheap proxy for
        # "legend" — then by debut (earlier-era players break ties).
        entries.sort(key=lambda e: (-e["_n"], e["_first"], e["birth"]))
        del entries[KEEP_PER_DAY:]
        for e in entries:
            # Backfill display years from swept seasons when exact dates are
            # missing (common for pre-war players), then drop the scratch keys.
            if not e.get("debut"):
                e["debut"] = f"{e['_first']}-01-01"
            if not e.get("last") and e["_last"] < this_year:
                e["last"] = f"{e['_last']}-12-31"
            e["seasons"] = e["_n"]
            for k in ("_first", "_last", "_n"):
                e.pop(k, None)

    payload = {
        "generated": time.strftime("%Y-%m-%d", time.gmtime()),
        "first_season": FIRST_SEASON,
        "last_season": this_year,
        "keep_per_day": KEEP_PER_DAY,
        "days": dict(sorted(days.items())),
    }

    STATIC.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(payload, separators=(",", ":")))
    elapsed = time.time() - start
    total = sum(len(v) for v in days.values())
    print(
        f"\nWrote {OUT.relative_to(STATIC.parent)} — "
        f"{len(by_id)} unique players, {total} kept across {len(days)} days, "
        f"{OUT.stat().st_size // 1024} KB, in {elapsed:.0f}s"
    )


if __name__ == "__main__":
    main()
