#!/usr/bin/env python3
"""Experiment 1: DNS-based IP collision detection."""

from __future__ import annotations

import argparse
import json
import socket
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = ROOT_DIR / "data" / "exp1_ip_coll_domains.json"
LOG_DIR = ROOT_DIR / "log"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H:%M:%S")


def load_targets(input_path: Path) -> list[dict[str, str]]:
    with input_path.open("r", encoding="utf-8") as f:
        raw = json.load(f)

    def normalize_item(item: Any) -> dict[str, str] | None:
        if not isinstance(item, dict):
            return None
        domain_raw = item.get("domain")
        if domain_raw is None:
            return None
        domain = str(domain_raw).strip().lower()
        if not domain:
            return None
        platform = str(item.get("platform", "")).strip() or domain
        return {"platform": platform, "domain": domain}

    targets: list[dict[str, str]] = []
    seen_domains: set[str] = set()

    def add_item(item: Any) -> None:
        normalized = normalize_item(item)
        if normalized is None:
            return
        domain = normalized["domain"]
        if domain in seen_domains:
            return
        seen_domains.add(domain)
        targets.append(normalized)

    if isinstance(raw, list):
        for item in raw:
            add_item(item)
    elif isinstance(raw, dict):
        for bucket in ("academic", "entertainment", "personal"):
            bucket_items = raw.get(bucket, [])
            if not isinstance(bucket_items, list):
                continue
            for item in bucket_items:
                add_item(item)
    else:
        raise ValueError("Input JSON must be a list or a map of target lists")

    return targets


def resolve_domain_ips(domain: str) -> tuple[list[str], str | None]:
    try:
        addrinfo = socket.getaddrinfo(domain, None, type=socket.SOCK_STREAM)
        ips = sorted({entry[4][0] for entry in addrinfo})
        return ips, None
    except socket.gaierror as exc:
        return [], f"gaierror: {exc}"
    except Exception as exc:  # noqa: BLE001
        return [], f"error: {exc}"


def collect_repeated_dns(
    targets: list[dict[str, str]],
    repetitions: int,
    wait_seconds: int,
    stamp: str,
) -> dict[str, Any]:
    rounds: list[dict[str, Any]] = []

    for repetition in range(1, repetitions + 1):
        polled_at = utc_now_iso()
        records: list[dict[str, Any]] = []
        for target in targets:
            ips, err = resolve_domain_ips(target["domain"])
            records.append(
                {
                    "platform": target["platform"],
                    "domain": target["domain"],
                    "ips": ips,
                    "error": err,
                }
            )

        rounds.append(
            {
                "repetition": repetition,
                "polled_at_utc": polled_at,
                "records": records,
            }
        )

        if repetition < repetitions:
            time.sleep(wait_seconds)

    return {
        "experiment": "exp1_ip_coll_dns_probe",
        "run_timestamp": stamp,
        "created_at_utc": utc_now_iso(),
        "repetitions": repetitions,
        "wait_seconds_between_repetitions": wait_seconds,
        "target_count": len(targets),
        "rounds": rounds,
    }


def build_all_ips_report(observations: dict[str, Any]) -> dict[str, Any]:
    per_domain: dict[str, dict[str, Any]] = {}
    for round_data in observations["rounds"]:
        for record in round_data["records"]:
            domain = record["domain"]
            domain_entry = per_domain.setdefault(
                domain,
                {
                    "platform": record["platform"],
                    "domain": domain,
                    "all_unique_ips": set(),
                    "polls_with_error": 0,
                    "ip_sets": set(),
                },
            )
            ips = record["ips"]
            if record["error"]:
                domain_entry["polls_with_error"] += 1
            domain_entry["all_unique_ips"].update(ips)
            domain_entry["ip_sets"].add(tuple(ips))

    final_domains: list[dict[str, Any]] = []
    for domain_data in per_domain.values():
        ip_set_variants = len(domain_data["ip_sets"])
        final_domains.append(
            {
                "platform": domain_data["platform"],
                "domain": domain_data["domain"],
                "all_ips": sorted(domain_data["all_unique_ips"]),
                "polls_with_error": domain_data["polls_with_error"],
                "ip_set_variants": ip_set_variants,
                "changed_across_polls": ip_set_variants > 1,
            }
        )

    final_domains.sort(key=lambda x: x["domain"])
    return {
        "experiment": observations["experiment"],
        "run_timestamp": observations["run_timestamp"],
        "created_at_utc": utc_now_iso(),
        "repetitions": observations["repetitions"],
        "domain_count": len(final_domains),
        "domains": final_domains,
    }


def build_collision_report(observations: dict[str, Any]) -> dict[str, Any]:
    ip_to_domains: dict[str, set[str]] = defaultdict(set)
    ip_to_platforms: dict[str, set[str]] = defaultdict(set)
    ip_to_repetitions: dict[str, set[int]] = defaultdict(set)

    for round_data in observations["rounds"]:
        rep = int(round_data["repetition"])
        for record in round_data["records"]:
            domain = record["domain"]
            platform = record["platform"]
            for ip in record["ips"]:
                ip_to_domains[ip].add(domain)
                ip_to_platforms[ip].add(platform)
                ip_to_repetitions[ip].add(rep)

    collisions = []
    for ip, domains in ip_to_domains.items():
        if len(domains) > 1:
            collisions.append(
                {
                    "ip": ip,
                    "domain_count": len(domains),
                    "domains": sorted(domains),
                    "platforms": sorted(ip_to_platforms[ip]),
                    "repetitions_seen": sorted(ip_to_repetitions[ip]),
                }
            )

    collisions.sort(key=lambda x: (-x["domain_count"], x["ip"]))
    return {
        "experiment": observations["experiment"],
        "run_timestamp": observations["run_timestamp"],
        "created_at_utc": utc_now_iso(),
        "collision_count": len(collisions),
        "collisions": collisions,
    }


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=False)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run repeated DNS lookups and detect potential IP collisions."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        help=f"Input JSON file of domain targets (default: {DEFAULT_INPUT})",
    )
    parser.add_argument(
        "--repetitions",
        type=int,
        default=1,
        help="Total DNS polling repetitions (default: 1)",
    )
    parser.add_argument(
        "--wait-seconds",
        type=int,
        default=60,
        help="Seconds to wait between repetitions (default: 60)",
    )
    parser.add_argument(
        "--all-ips-output",
        type=Path,
        default=None,
        help="Optional all-IPs report output path",
    )
    parser.add_argument(
        "--collisions-output",
        type=Path,
        default=None,
        help="Optional collision report output path",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    targets = load_targets(args.input)
    stamp = run_timestamp()
    all_ips_output = args.all_ips_output or LOG_DIR / f"exp1_ip_coll_all_ips_{stamp}.json"
    collisions_output = (
        args.collisions_output
        or LOG_DIR / f"exp1_ip_coll_possible_collisions_{stamp}.json"
    )

    observations = collect_repeated_dns(
        targets=targets,
        repetitions=args.repetitions,
        wait_seconds=args.wait_seconds,
        stamp=stamp,
    )
    all_ips_report = build_all_ips_report(observations)
    collision_report = build_collision_report(observations)

    write_json(all_ips_output, all_ips_report)
    write_json(collisions_output, collision_report)

    print(json.dumps(all_ips_report, indent=2))
    print(json.dumps(collision_report, indent=2))


if __name__ == "__main__":
    main()
