#!/usr/bin/env bash
# Smoke test against the live site. Run daily by .github/workflows/smoke.yml;
# also runnable by hand:  tools/smoke.sh [base-url]
#
# Checks a handful of routes for a 200 plus a marker that only a correct render
# contains, a known-bad route for a real 404 (scripts rely on `curl -f`), and the
# TLS certs: Cloudflare's edge cert AND Fly's origin cert behind it. The origin
# cert is the one that bit us — it expired behind Cloudflare and every route
# answered 525 while the app itself was fine.
set -uo pipefail

BASE="${1:-https://mlbsched.run}"
HOST="${SMOKE_HOST:-mlbsched.run}"
ORIGIN="${SMOKE_ORIGIN:-mlbsched.fly.dev}"
MIN_CERT_DAYS="${SMOKE_MIN_CERT_DAYS:-14}"

failures=0
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

ok()   { printf 'ok    %s\n' "$*"; }
fail() { printf 'FAIL  %s\n' "$*"; failures=$((failures + 1)); }

# GET a path into $tmp/body; sets $ctype. --retry rides out a cold start
# (min_machines_running = 0) and a blip upstream before calling it a failure.
fetch() {
    ctype="$(curl -fsS --max-time 30 --retry 2 --retry-delay 10 --retry-all-errors \
        -A "curl/smoke (mlbsched.run smoke test)" \
        -o "$tmp/body" -w '%{content_type}' "$BASE$1" 2>"$tmp/err")"
}

# Text route: 200, plain text (the curl view), and contains a marker.
check_text() {
    local path="$1" marker="$2"
    if ! fetch "$path"; then
        fail "$path — $(tr -d '\n' <"$tmp/err")"
    elif [[ "$ctype" != text/plain* ]]; then
        fail "$path — expected text/plain, got '$ctype'"
    elif ! grep -qF -- "$marker" "$tmp/body"; then
        fail "$path — 200 but missing '$marker'"
    else
        ok "$path"
    fi
}

# JSON route: 200, parses, and passes a Python assertion over `d`.
check_json() {
    local path="$1" assertion="$2"
    if ! fetch "$path"; then
        fail "$path — $(tr -d '\n' <"$tmp/err")"
    elif ! python3 -c "import json,sys; d=json.load(open(sys.argv[1])); assert $assertion" \
            "$tmp/body" 2>"$tmp/err"; then
        fail "$path — JSON check failed: $assertion ($(tail -n1 "$tmp/err"))"
    else
        ok "$path"
    fi
}

check_status() {
    local path="$1" want="$2" got
    got="$(curl -sS --max-time 30 -A "curl/smoke" -o /dev/null -w '%{http_code}' "$BASE$path")"
    if [[ "$got" == "$want" ]]; then ok "$path → $got"; else fail "$path — expected $want, got $got"; fi
}

# connect-host:port, the name the cert must cover, and a label for the output.
check_cert() {
    local connect="$1" name="$2" label="$3" pem end days
    pem="$(openssl s_client -connect "$connect" -servername "$name" </dev/null 2>/dev/null \
        | openssl x509 2>/dev/null)"
    if [[ -z "$pem" ]]; then
        fail "$label cert — no certificate from $connect"
        return
    fi
    end="$(openssl x509 -noout -enddate <<<"$pem" | cut -d= -f2)"
    days="$(python3 -c "import sys,datetime as d; t=d.datetime.strptime(sys.argv[1], '%b %d %H:%M:%S %Y %Z').replace(tzinfo=d.timezone.utc); print((t - d.datetime.now(d.timezone.utc)).days)" "$end")"
    if ! openssl x509 -noout -checkhost "$name" <<<"$pem" | grep -q 'does match'; then
        # e.g. Fly serving its *.fly.dev fallback because the mlbsched.run cert is gone
        fail "$label cert — doesn't cover $name ($(openssl x509 -noout -subject <<<"$pem"))"
    elif ! openssl x509 -noout -checkend $((MIN_CERT_DAYS * 86400)) <<<"$pem" >/dev/null; then
        fail "$label cert — expires $end ($days days left, minimum $MIN_CERT_DAYS)"
    else
        ok "$label cert — expires $end ($days days left)"
    fi
}

echo "smoke test: $BASE  ($(date -u '+%Y-%m-%d %H:%M UTC'))"

check_text  /           "MLB Schedule"
check_text  /help       "curl mlbsched.run/standings"
check_text  /standings  "MLB Standings"
check_json  /api/teams  "len(d['teams']) == 30 and all({'team', 'id', 'name'} <= set(t) for t in d['teams'])"
check_json  /api/season "{'date', 'phase', 'stats_season', 'schedule_season', 'dates'} <= set(d) and d['phase'] in ('preseason', 'spring', 'regular', 'allstar', 'postseason', 'offseason')"

if fetch /ical/NYM.ics; then
    events="$(grep -c '^BEGIN:VEVENT' "$tmp/body")"
    if head -n1 "$tmp/body" | grep -q '^BEGIN:VCALENDAR' && [[ "$events" -ge 100 ]]; then
        ok "/ical/NYM.ics ($events events)"
    else
        fail "/ical/NYM.ics — not a full calendar ($events events)"
    fi
else
    fail "/ical/NYM.ics — $(tr -d '\n' <"$tmp/err")"
fi

if fetch /og.png && [[ "$ctype" == image/png && "$(head -c 4 "$tmp/body" | tail -c 3)" == PNG ]]; then
    ok "/og.png"
else
    fail "/og.png — expected a PNG, got '${ctype:-no response}'"
fi

check_status /lineup/ZZZ 404

check_cert "$HOST:443"   "$HOST" "edge (Cloudflare)"
check_cert "$ORIGIN:443" "$HOST" "origin (Fly)"

if (( failures )); then
    echo "$failures check(s) failed"
    exit 1
fi
echo "all checks passed"
