#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SRC_DIR="$ROOT_DIR/src"
LOG_DIR="$ROOT_DIR/log"
DATA_DIR="$ROOT_DIR/data"

ACTIVE_STATE_FILE="$LOG_DIR/run1_active.json"
STAMP="$(date +"%Y%m%d-%H:%M:%S")"
STATE_FILE="$LOG_DIR/run1_state_${STAMP}.json"

ORDERED_LOG="$LOG_DIR/ordered_domains_${STAMP}.txt"
SPLIT_LOG="$LOG_DIR/domain_splits_${STAMP}.json"
INTERMEDIATE_LOG="$LOG_DIR/youtube_block_intermediate_${STAMP}.json"
FINAL_SUMMARY_TXT="$LOG_DIR/youtube_block_summary_${STAMP}.txt"
FINAL_SUMMARY_JSON="$LOG_DIR/youtube_block_summary_${STAMP}.json"

BLOCKER_PID_FILE="$LOG_DIR/youtube_blocker_${STAMP}.pid"
BLOCKER_STDOUT="$LOG_DIR/youtube_blocker_stdout_${STAMP}.log"
BROWSER_STDOUT="$LOG_DIR/browser_stdout_${STAMP}.log"

mkdir -p "$LOG_DIR" "$DATA_DIR"

if [[ -f "$ACTIVE_STATE_FILE" ]]; then
  echo "A run1 session is already marked active: $ACTIVE_STATE_FILE"
  echo "Run src/close1.sh first, or remove stale state if no processes are running."
  exit 1
fi

if [[ -f "$LOG_DIR/dns_middleman.pid" ]]; then
  EXISTING_DNS_PID="$(cat "$LOG_DIR/dns_middleman.pid" 2>/dev/null || true)"
  if [[ -n "$EXISTING_DNS_PID" ]] && sudo kill -0 "$EXISTING_DNS_PID" 2>/dev/null; then
    echo "dns_middleman is already running (PID $EXISTING_DNS_PID)."
    echo "Run src/close1.sh (or src/run_background.sh stop) before starting a new run1 session."
    exit 1
  fi
fi

echo "Starting DNS middleman..."
sudo "$SRC_DIR/run_background.sh" start -- --listen-port 53 --run-stamp "$STAMP"
DNS_PID="$(cat "$LOG_DIR/dns_middleman.pid" 2>/dev/null || true)"

echo "Routing system DNS to 127.0.0.1..."
sudo python3 "$SRC_DIR/macos_dns_config.py" apply-local --local-dns 127.0.0.1 --log-dir "$LOG_DIR"

echo "Starting YouTube IP blocker..."
sudo nohup python3 "$SRC_DIR/youtube_ip_blocker.py" \
  --ordered-log "$ORDERED_LOG" \
  --split-log "$SPLIT_LOG" \
  --log-dir "$LOG_DIR" \
  --run-stamp "$STAMP" \
  --target-domain youtube.com \
  --pid-file "$BLOCKER_PID_FILE" \
  >"$BLOCKER_STDOUT" 2>&1 &

BLOCKER_PID=""
for _ in {1..80}; do
  if [[ -s "$BLOCKER_PID_FILE" ]]; then
    BLOCKER_PID="$(cat "$BLOCKER_PID_FILE" 2>/dev/null || true)"
    break
  fi
  sleep 0.1
done

if [[ -z "$BLOCKER_PID" ]]; then
  echo "YouTube IP blocker failed to start (missing pid file: $BLOCKER_PID_FILE)." >&2
  sudo "$SRC_DIR/run_background.sh" stop || true
  sudo python3 "$SRC_DIR/macos_dns_config.py" restore --log-dir "$LOG_DIR" || true
  exit 1
fi

echo "Priming DNS for youtube.com and www.youtube.com through local resolver..."
python3 - <<'PY' || true
import socket

for host in ("youtube.com", "www.youtube.com"):
    try:
        socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        pass
PY

echo "Waiting for blocker to arm..."
ARMED=0
for _ in {1..100}; do
  if [[ -s "$INTERMEDIATE_LOG" ]]; then
    if python3 - "$INTERMEDIATE_LOG" <<'PY'
import json
import sys

path = sys.argv[1]
try:
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
except Exception:
    raise SystemExit(1)

blocked_ips = payload.get("blocked_ips", [])
if isinstance(blocked_ips, list) and len(blocked_ips) > 0:
    raise SystemExit(0)
raise SystemExit(1)
PY
    then
      ARMED=1
      break
    fi
  fi
  sleep 0.2
done

if [[ "$ARMED" -eq 1 ]]; then
  echo "Blocker is armed with YouTube IPs."
else
  echo "Warning: blocker did not report blocked YouTube IPs yet; continuing." >&2
fi

echo "Launching browser and opening youtube.com..."
nohup python3 "$SRC_DIR/browser.py" --url https://youtube.com >"$BROWSER_STDOUT" 2>&1 &
BROWSER_PID="$!"

STAMP="$STAMP" \
STATE_FILE="$STATE_FILE" \
DNS_PID="$DNS_PID" \
BLOCKER_PID="$BLOCKER_PID" \
BROWSER_PID="$BROWSER_PID" \
ORDERED_LOG="$ORDERED_LOG" \
SPLIT_LOG="$SPLIT_LOG" \
INTERMEDIATE_LOG="$INTERMEDIATE_LOG" \
FINAL_SUMMARY_TXT="$FINAL_SUMMARY_TXT" \
FINAL_SUMMARY_JSON="$FINAL_SUMMARY_JSON" \
BLOCKER_PID_FILE="$BLOCKER_PID_FILE" \
BLOCKER_STDOUT="$BLOCKER_STDOUT" \
BROWSER_STDOUT="$BROWSER_STDOUT" \
python3 - <<'PY'
import json
import os
from datetime import datetime, timezone
from pathlib import Path

state = {
    "run_stamp": os.environ["STAMP"],
    "started_at_utc": datetime.now(timezone.utc).isoformat(),
    "target_domain": "youtube.com",
    "dns_pid": os.environ.get("DNS_PID") or "",
    "blocker_pid": os.environ.get("BLOCKER_PID") or "",
    "browser_pid": os.environ.get("BROWSER_PID") or "",
    "ordered_log": os.environ["ORDERED_LOG"],
    "split_log": os.environ["SPLIT_LOG"],
    "intermediate_log": os.environ["INTERMEDIATE_LOG"],
    "final_summary_txt": os.environ["FINAL_SUMMARY_TXT"],
    "final_summary_json": os.environ["FINAL_SUMMARY_JSON"],
    "blocker_pid_file": os.environ["BLOCKER_PID_FILE"],
    "blocker_stdout_log": os.environ["BLOCKER_STDOUT"],
    "browser_stdout_log": os.environ["BROWSER_STDOUT"],
}
Path(os.environ["STATE_FILE"]).write_text(json.dumps(state, indent=2), encoding="utf-8")
PY

cp "$STATE_FILE" "$ACTIVE_STATE_FILE"

close_once=0
graceful_close() {
  if [[ "$close_once" -eq 1 ]]; then
    return
  fi
  close_once=1
  "$SRC_DIR/close1.sh" --state "$STATE_FILE"
}

trap graceful_close INT TERM EXIT

echo ""
echo "Experiment is running."
echo "State file:        $STATE_FILE"
echo "Ordered DNS log:   $ORDERED_LOG"
echo "Intermediate log:  $INTERMEDIATE_LOG"
echo "Final summary txt: $FINAL_SUMMARY_TXT"
echo ""
echo "Press Enter to gracefully stop run1..."
read -r _ || true
graceful_close
