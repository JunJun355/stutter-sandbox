#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
SCRIPT_PATH="$ROOT_DIR/src/dns_middleman.py"
LOG_DIR="$ROOT_DIR/log"
PID_FILE="$LOG_DIR/dns_middleman.pid"

usage() {
  echo "Usage:"
  echo "  $0 start [-- <extra middleman args>]"
  echo "  $0 stop"
  echo "  $0 status"
}

is_running() {
  [[ -f "$PID_FILE" ]] || return 1
  local pid
  pid="$(cat "$PID_FILE")"
  kill -0 "$pid" 2>/dev/null
}

cmd="${1:-}"
if [[ -z "$cmd" ]]; then
  usage
  exit 1
fi
shift || true

mkdir -p "$LOG_DIR"

case "$cmd" in
  start)
    if is_running; then
      echo "Middleman already running with PID $(cat "$PID_FILE")"
      exit 0
    fi
    if [[ "${1:-}" == "--" ]]; then
      shift
    fi
    ts="$(date +"%Y%m%d-%H:%M:%S")"
    out_log="$LOG_DIR/dns_middleman_stdout_${ts}.log"
    nohup python3 "$SCRIPT_PATH" "$@" >"$out_log" 2>&1 &
    pid="$!"
    echo "$pid" > "$PID_FILE"
    echo "Started middleman PID $pid"
    echo "Stdout log: $out_log"
    ;;
  stop)
    if ! is_running; then
      echo "Middleman is not running."
      rm -f "$PID_FILE"
      exit 0
    fi
    pid="$(cat "$PID_FILE")"
    kill -TERM "$pid" 2>/dev/null || true
    for _ in {1..20}; do
      if kill -0 "$pid" 2>/dev/null; then
        sleep 0.2
      else
        break
      fi
    done
    if kill -0 "$pid" 2>/dev/null; then
      kill -KILL "$pid" 2>/dev/null || true
    fi
    rm -f "$PID_FILE"
    echo "Stopped middleman PID $pid"
    ;;
  status)
    if is_running; then
      echo "Middleman is running with PID $(cat "$PID_FILE")"
    else
      echo "Middleman is not running."
    fi
    ;;
  *)
    usage
    exit 1
    ;;
esac
