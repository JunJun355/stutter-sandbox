#!/usr/bin/env python3
"""Simple Chromium launcher for exp3 tests.

Goal:
- Use a controllable browser session for DNS experiments.
- Disable DNS-over-HTTPS-related features so hostname resolution uses system DNS.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
LOG_DIR = ROOT_DIR / "log"
PROFILE_DIR = DATA_DIR / "browser_profile"


def run_timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H:%M:%S")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Launch a local Chromium session with DoH disabled."
    )
    parser.add_argument(
        "--url",
        default="https://example.com",
        help="Initial URL to open (default: https://example.com)",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run in headless mode (default: false)",
    )
    parser.add_argument(
        "--proxy",
        default="",
        help="Optional proxy URL, for example http://127.0.0.1:8080",
    )
    parser.add_argument(
        "--channel",
        default="chromium",
        help="Playwright browser channel (default: chromium)",
    )
    return parser.parse_args()


def build_launch_args() -> list[str]:
    # Keep DNS resolution on the system resolver path by disabling DoH-related features.
    return [
        "--disable-dns-over-https",
        "--disable-features=DnsOverHttps,UseDnsHttpsSvcb,UseDnsHttpsSvcbAlpn,AsyncDns",
        "--disable-quic",
    ]


def ensure_paths() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)


def write_session_log(payload: dict[str, Any]) -> Path:
    out_path = LOG_DIR / f"browser_session_{run_timestamp()}.json"
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=False)
    return out_path


def main() -> int:
    args = parse_args()
    ensure_paths()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("playwright is not installed.", file=sys.stderr)
        print(
            "Install with:\n"
            "  python3 -m pip install playwright\n"
            "  python3 -m playwright install chromium",
            file=sys.stderr,
        )
        return 1

    launch_args = build_launch_args()
    session_meta: dict[str, Any] = {
        "started_at_utc": utc_now_iso(),
        "initial_url": args.url,
        "headless": args.headless,
        "proxy": args.proxy or None,
        "channel": args.channel,
        "chromium_args": launch_args,
        "profile_dir": str(PROFILE_DIR),
    }
    session_log_path = write_session_log(session_meta)
    print(f"Session config log: {session_log_path}")

    proxy_cfg = {"server": args.proxy} if args.proxy else None

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE_DIR),
            channel=args.channel,
            headless=args.headless,
            args=launch_args,
            proxy=proxy_cfg,
        )

        page = context.pages[0] if context.pages else context.new_page()
        page.goto(args.url, wait_until="domcontentloaded")
        print("Browser launched. Press Ctrl+C to close.")
        print(f"Opened: {args.url}")

        try:
            page.wait_for_timeout(24 * 60 * 60 * 1000)
        except KeyboardInterrupt:
            pass
        finally:
            context.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
