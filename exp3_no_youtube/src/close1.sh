#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SRC_DIR="$ROOT_DIR/src"
LOG_DIR="$ROOT_DIR/log"
ACTIVE_STATE_FILE="$LOG_DIR/run1_active.json"

STATE_FILE=""
RUN_STAMP_ARG=""

usage() {
  echo "Usage:"
  echo "  $0 [--state log/run1_state_YYYYMMDD-HH:MM:SS.json]"
  echo "  $0 [--run-stamp YYYYMMDD-HH:MM:SS]"
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --state)
      STATE_FILE="${2:-}"
      shift 2
      ;;
    --run-stamp)
      RUN_STAMP_ARG="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage
      exit 1
      ;;
  esac
done

if [[ -z "$STATE_FILE" ]]; then
  if [[ -n "$RUN_STAMP_ARG" ]]; then
    STATE_FILE="$LOG_DIR/run1_state_${RUN_STAMP_ARG}.json"
  elif [[ -f "$ACTIVE_STATE_FILE" ]]; then
    STATE_FILE="$ACTIVE_STATE_FILE"
  else
    STATE_FILE="$(ls -1t "$LOG_DIR"/run1_state_*.json 2>/dev/null | head -n 1 || true)"
  fi
fi

if [[ -z "$STATE_FILE" || ! -f "$STATE_FILE" ]]; then
  echo "No run1 state file found to close." >&2
  exit 1
fi

eval "$(
  STATE_FILE="$STATE_FILE" python3 - <<'PY'
import json
import os
import shlex
from pathlib import Path

path = Path(os.environ["STATE_FILE"])
state = json.loads(path.read_text(encoding="utf-8"))

keys = [
    "run_stamp",
    "target_domain",
    "dns_pid",
    "blocker_pid",
    "browser_pid",
    "ordered_log",
    "split_log",
    "intermediate_log",
    "final_summary_txt",
    "final_summary_json",
    "blocker_pid_file",
]

for key in keys:
    value = state.get(key, "")
    if value is None:
        value = ""
    print(f"{key.upper()}={shlex.quote(str(value))}")
PY
)"

kill_user_pid() {
  local pid="$1"
  if [[ -z "$pid" ]]; then
    return
  fi
  if ! kill -0 "$pid" 2>/dev/null; then
    return
  fi
  kill -TERM "$pid" 2>/dev/null || true
  for _ in {1..50}; do
    if kill -0 "$pid" 2>/dev/null; then
      sleep 0.1
    else
      return
    fi
  done
  kill -KILL "$pid" 2>/dev/null || true
}

kill_root_pid_graceful() {
  local pid="$1"
  if [[ -z "$pid" ]]; then
    return
  fi
  if ! sudo kill -0 "$pid" 2>/dev/null; then
    return
  fi

  sudo kill -INT "$pid" 2>/dev/null || true
  for _ in {1..80}; do
    if sudo kill -0 "$pid" 2>/dev/null; then
      sleep 0.1
    else
      return
    fi
  done

  sudo kill -TERM "$pid" 2>/dev/null || true
  for _ in {1..50}; do
    if sudo kill -0 "$pid" 2>/dev/null; then
      sleep 0.1
    else
      return
    fi
  done

  sudo kill -KILL "$pid" 2>/dev/null || true
}

echo "Closing run1 session: ${RUN_STAMP:-unknown}"

# 1) Close browser process.
kill_user_pid "${BROWSER_PID:-}"

# 2) Stop blocker so it writes final summary logs.
kill_root_pid_graceful "${BLOCKER_PID:-}"
if [[ -n "${FINAL_SUMMARY_TXT:-}" ]]; then
  for _ in {1..80}; do
    if [[ -f "$FINAL_SUMMARY_TXT" ]]; then
      break
    fi
    sleep 0.1
  done
fi

# 3) Stop DNS middleman.
sudo "$SRC_DIR/run_background.sh" stop || true

# 4) Restore DNS settings from latest backup.
sudo python3 "$SRC_DIR/macos_dns_config.py" restore --log-dir "$LOG_DIR" || true

# 5) Safety cleanup: remove PF rules in experiment anchor.
sudo pfctl -a com.apple/exp3_no_youtube -F rules >/dev/null 2>&1 || true
sudo pfctl -a com.apple/exp3_no_youtube -F Tables >/dev/null 2>&1 || true

# 6) Remove stale pid file if present.
if [[ -n "${BLOCKER_PID_FILE:-}" ]]; then
  rm -f "$BLOCKER_PID_FILE"
fi

# 7) Clear active marker when it points to this run stamp.
if [[ -f "$ACTIVE_STATE_FILE" ]]; then
  ACTIVE_STAMP="$(
    STATE_FILE="$ACTIVE_STATE_FILE" python3 - <<'PY'
import json
import os
from pathlib import Path

path = Path(os.environ["STATE_FILE"])
if not path.exists():
    print("")
    raise SystemExit(0)
try:
    obj = json.loads(path.read_text(encoding="utf-8"))
except Exception:
    print("")
    raise SystemExit(0)
print(obj.get("run_stamp", ""))
PY
  )"
  if [[ "${ACTIVE_STAMP:-}" == "${RUN_STAMP:-}" ]]; then
    rm -f "$ACTIVE_STATE_FILE"
  fi
fi

echo "Closed run1 session."
if [[ -n "${INTERMEDIATE_LOG:-}" ]]; then
  echo "Intermediate log: ${INTERMEDIATE_LOG}"
fi
if [[ -n "${FINAL_SUMMARY_TXT:-}" ]]; then
  echo "Final summary txt: ${FINAL_SUMMARY_TXT}"
fi
if [[ -n "${FINAL_SUMMARY_JSON:-}" ]]; then
  echo "Final summary json: ${FINAL_SUMMARY_JSON}"
fi
