#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
DATA_DIR="$ROOT_DIR/data"
LOG_DIR="$ROOT_DIR/log"
SRC_DIR="$ROOT_DIR/src"
EXP2_SPLITS="$ROOT_DIR/../exp2_dns_middleman/data/splits.json"
EXP3_SPLITS="$DATA_DIR/splits.json"

mkdir -p "$DATA_DIR" "$LOG_DIR"

if [[ ! -f "$EXP3_SPLITS" && -f "$EXP2_SPLITS" ]]; then
  cp "$EXP2_SPLITS" "$EXP3_SPLITS"
  echo "Copied domain splits into: $EXP3_SPLITS"
elif [[ -f "$EXP3_SPLITS" ]]; then
  echo "Domain splits already present: $EXP3_SPLITS"
else
  echo "No splits.json found in exp2 or exp3; continuing without it."
fi

chmod +x \
  "$SRC_DIR/dns_middleman.py" \
  "$SRC_DIR/macos_dns_config.py" \
  "$SRC_DIR/run_background.sh" \
  "$SRC_DIR/browser.py" \
  "$SRC_DIR/youtube_ip_blocker.py" \
  "$SRC_DIR/run1.sh" \
  "$SRC_DIR/close1.sh" || true

if ! command -v python3 >/dev/null 2>&1; then
  echo "python3 is not available in PATH." >&2
  exit 1
fi
if ! command -v networksetup >/dev/null 2>&1; then
  echo "networksetup is not available (required on macOS)." >&2
  exit 1
fi
if ! command -v pfctl >/dev/null 2>&1; then
  echo "pfctl is not available (required for IP blocking)." >&2
  exit 1
fi

python3 - <<'PY'
import importlib.util
import sys

missing = []
if importlib.util.find_spec("playwright") is None:
    missing.append("playwright")

if missing:
    print("Missing Python package(s): " + ", ".join(missing), file=sys.stderr)
    print("Install with:", file=sys.stderr)
    print("  python3 -m pip install playwright", file=sys.stderr)
    print("  python3 -m playwright install chromium", file=sys.stderr)
    raise SystemExit(1)
PY

echo "init1 complete."
echo "Next: run src/run1.sh"
