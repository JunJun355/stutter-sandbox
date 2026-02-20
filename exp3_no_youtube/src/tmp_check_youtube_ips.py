#!/usr/bin/env python3
"""Temporary self-contained DNS churn checker for YouTube-related domains.

Repeatedly resolves domains and reports whether A/AAAA sets changed.
"""

from __future__ import annotations

import argparse
import json
import socket
import time
from collections import defaultdict
from datetime import datetime, timezone


def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H:%M:%S")


DEFAULT_DOMAINS = ["youtube.com", "www.youtube.com", "i.ytimg.com"]


def resolve_ips_by_family(domain: str) -> tuple[list[str], list[str]]:
    ipv4: set[str] = set()
    ipv6: set[str] = set()

    try:
        infos4 = socket.getaddrinfo(
            domain, 443, family=socket.AF_INET, type=socket.SOCK_STREAM
        )
        ipv4.update(item[4][0] for item in infos4)
    except OSError:
        pass

    try:
        infos6 = socket.getaddrinfo(
            domain, 443, family=socket.AF_INET6, type=socket.SOCK_STREAM
        )
        ipv6.update(item[4][0] for item in infos6)
    except OSError:
        pass

    return sorted(ipv4), sorted(ipv6)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Repeatedly resolve domains and detect A/AAAA set changes."
    )
    parser.add_argument(
        "--domains",
        nargs="+",
        default=DEFAULT_DOMAINS,
        help=(
            "Domains to resolve each round "
            "(default: youtube.com www.youtube.com i.ytimg.com)"
        ),
    )
    parser.add_argument("--repetitions", type=int, default=20)
    parser.add_argument("--interval-seconds", type=float, default=3.0)
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print one JSON object per line instead of text.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    domains = [d.strip().lower().strip(".") for d in args.domains if d.strip()]
    previous: dict[str, tuple[set[str], set[str]]] = {}
    changed_rounds_by_domain: dict[str, int] = defaultdict(int)

    for i in range(1, args.repetitions + 1):
        ts = now_utc()
        for domain in domains:
            ipv4, ipv6 = resolve_ips_by_family(domain)
            current = (set(ipv4), set(ipv6))
            old = previous.get(domain)
            changed = old is not None and old != current
            if changed:
                changed_rounds_by_domain[domain] += 1
            previous[domain] = current

            if args.json:
                payload = {
                    "round": i,
                    "time_utc": ts,
                    "domain": domain,
                    "ipv4": ipv4,
                    "ipv6": ipv6,
                    "changed_from_previous": changed,
                }
                print(json.dumps(payload, separators=(",", ":")))
            else:
                status = "CHANGED" if changed else "same_or_first"
                v4 = ", ".join(ipv4) if ipv4 else "(none)"
                v6 = ", ".join(ipv6) if ipv6 else "(none)"
                print(f"[{i:03d}] {ts} {status} {domain} A=[{v4}] AAAA=[{v6}]")

        if i < args.repetitions:
            time.sleep(max(args.interval_seconds, 0.0))

    if args.json:
        summary = {
            "summary": True,
            "domains": domains,
            "repetitions": args.repetitions,
            "changed_rounds_by_domain": dict(changed_rounds_by_domain),
        }
        print(json.dumps(summary, separators=(",", ":")))
    else:
        for domain in domains:
            print(
                f"Summary: domain={domain} repetitions={args.repetitions} "
                f"changed_rounds={changed_rounds_by_domain.get(domain, 0)}"
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
