#!/usr/bin/env python3
"""Exp3 YouTube IP blocker.

Reads ordered DNS events from exp3 middleman output, extracts IPs returned
for youtube.com, and installs PF egress drop rules for those IPs.

Artifacts:
- youtube_block_intermediate_<stamp>.json (continuously updated)
- youtube_block_summary_<stamp>.json (written at shutdown)
- youtube_block_summary_<stamp>.txt (human-readable final summary)
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from typing import Any


ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_LOG_DIR = ROOT_DIR / "log"
ORDERED_LINE_RE = re.compile(
    r"^\[(?P<domain>[^\]]*)\]\s*@\s*\[(?P<time>[^\]]*)\]\s*using\s*\[(?P<ips>[^\]]*)\]\s*$"
)


def run_timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H:%M:%S")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_cmd(
    args: list[str], *, input_text: str | None = None, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        input=input_text,
        text=True,
        capture_output=True,
        check=check,
    )


def normalize_ip(raw: str) -> str | None:
    token = raw.strip()
    if not token:
        return None
    try:
        return str(ipaddress.ip_address(token))
    except ValueError:
        return None


def parse_ordered_line(line: str) -> tuple[str, str, list[str]] | None:
    m = ORDERED_LINE_RE.match(line.strip())
    if m is None:
        return None

    domain = m.group("domain").strip().lower().strip(".")
    event_time = m.group("time").strip()
    ips_raw = m.group("ips").strip()

    seen: set[str] = set()
    ips: list[str] = []
    if ips_raw:
        for part in ips_raw.split(","):
            ip = normalize_ip(part)
            if ip and ip not in seen:
                seen.add(ip)
                ips.append(ip)

    return domain, event_time, ips


def build_target_domains(target_domain: str) -> set[str]:
    base = target_domain.lower().strip(".")
    targets = {base}
    if not base.startswith("www."):
        targets.add(f"www.{base}")
    return targets


def is_target_domain(domain: str, target_domains: set[str]) -> bool:
    return domain.lower().strip(".") in target_domains


def ensure_pf_enabled() -> tuple[bool, str]:
    info = run_cmd(["pfctl", "-s", "info"], check=False)
    if info.returncode == 0 and "Status: Enabled" in info.stdout:
        return True, "pf already enabled"

    enable = run_cmd(["pfctl", "-E"], check=False)
    if enable.returncode == 0:
        msg = (enable.stdout.strip() or enable.stderr.strip() or "pf enabled").strip()
        return True, msg

    reason = (enable.stderr.strip() or enable.stdout.strip() or "pfctl -E failed").strip()
    return False, reason


def clear_anchor_rules(anchor: str) -> None:
    run_cmd(["pfctl", "-a", anchor, "-F", "rules"], check=False)
    run_cmd(["pfctl", "-a", anchor, "-F", "Tables"], check=False)


def build_anchor_rules(ips: list[str], label: str) -> str:
    table_name = "exp3_yt_block_ips"
    joined = ", ".join(ips)
    return (
        f"table <{table_name}> persist {{ {joined} }}\n"
        f'block drop out quick to <{table_name}> label "{label}"\n'
    )


def apply_anchor_rules(anchor: str, ips: set[str], label: str) -> tuple[bool, str]:
    if not ips:
        clear_anchor_rules(anchor)
        return True, "cleared rules (no YouTube IPs yet)"

    ordered_ips = sorted(ips)
    rules = build_anchor_rules(ordered_ips, label)
    proc = run_cmd(["pfctl", "-a", anchor, "-f", "-"], input_text=rules, check=False)
    if proc.returncode != 0:
        reason = (proc.stderr.strip() or proc.stdout.strip() or "pfctl rule load failed").strip()
        return False, reason
    return True, f"loaded {len(ordered_ips)} blocked IPs into {anchor}"


def read_blocked_packet_count(anchor: str, label: str) -> int:
    proc = run_cmd(["pfctl", "-a", anchor, "-vvs", "rules"], check=False)
    if proc.returncode != 0:
        return 0

    packets_total = 0
    current_rule_matches_label = False
    has_packet_data = False

    for raw_line in proc.stdout.splitlines():
        line = raw_line.strip()
        if line.startswith("block"):
            current_rule_matches_label = label in line
        if not current_rule_matches_label:
            continue
        m = re.search(r"Packets:\s*(\d+)", line, flags=re.IGNORECASE)
        if m:
            packets_total += int(m.group(1))
            has_packet_data = True

    if has_packet_data:
        return packets_total

    # Fallback for platforms that expose label counters separately.
    labels_proc = run_cmd(["pfctl", "-a", anchor, "-s", "labels"], check=False)
    if labels_proc.returncode != 0:
        return 0
    if label not in labels_proc.stdout:
        return 0
    values = [int(v) for v in re.findall(r"Packets:\s*(\d+)", labels_proc.stdout)]
    return sum(values) if values else 0


@dataclass
class BlockerState:
    target_domain: str
    target_domains: set[str]
    run_stamp: str
    ordered_log: Path
    split_log: Path | None
    intermediate_log: Path
    final_summary_json: Path
    final_summary_txt: Path
    anchor: str
    label: str
    consumed_bytes: int = 0
    total_dns_events: int = 0
    youtube_dns_query_seen: bool = False
    youtube_dns_returned: bool = False
    youtube_events: list[dict[str, Any]] = field(default_factory=list)
    youtube_ips: set[str] = field(default_factory=set)
    blocked_ips: set[str] = field(default_factory=set)
    observed_domains: set[str] = field(default_factory=set)
    unblocked_domains: set[str] = field(default_factory=set)
    cumulative_blocked_packets: int = 0


def build_snapshot(
    state: BlockerState,
    *,
    blocked_packets: int,
    pf_ready: bool,
    last_pf_message: str,
) -> dict[str, Any]:
    return {
        "updated_at_utc": utc_now_iso(),
        "run_stamp": state.run_stamp,
        "target_domain": state.target_domain,
        "target_domains": sorted(state.target_domains),
        "ordered_log": str(state.ordered_log),
        "split_log": str(state.split_log) if state.split_log else None,
        "total_dns_events": state.total_dns_events,
        "youtube_dns_query_seen": state.youtube_dns_query_seen,
        "youtube_dns_returned": state.youtube_dns_returned,
        "youtube_dns_events": state.youtube_events,
        "youtube_ips": sorted(state.youtube_ips),
        "blocked_ips": sorted(state.blocked_ips),
        "observed_domains_count": len(state.observed_domains),
        "observed_domains": sorted(state.observed_domains),
        "unblocked_domains_count": len(state.unblocked_domains),
        "unblocked_domains": sorted(state.unblocked_domains),
        "pf_anchor": state.anchor,
        "pf_label": state.label,
        "pf_ready": pf_ready,
        "pf_status": last_pf_message,
        "blocked_packets_so_far": blocked_packets,
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2, sort_keys=False)


def write_final_txt(path: Path, payload: dict[str, Any]) -> None:
    youtube_ips = ", ".join(payload.get("youtube_ips", [])) or "(none)"
    blocked_ips = ", ".join(payload.get("blocked_ips", [])) or "(none)"
    unblocked_domains = ", ".join(payload.get("unblocked_domains", [])) or "(none)"

    lines = [
        f"run_stamp: {payload.get('run_stamp', '')}",
        f"target_domain: {payload.get('target_domain', '')}",
        f"youtube_dns_query_seen: {payload.get('youtube_dns_query_seen', False)}",
        f"youtube_dns_returned: {payload.get('youtube_dns_returned', False)}",
        f"youtube_dns_ips: {youtube_ips}",
        f"blocked_ips: {blocked_ips}",
        f"blocked_youtube_outgoing_packets: {payload.get('blocked_packets_so_far', 0)}",
        f"unblocked_domains: {unblocked_domains}",
        f"total_dns_events: {payload.get('total_dns_events', 0)}",
        f"observed_domains_count: {payload.get('observed_domains_count', 0)}",
        f"intermediate_log: {payload.get('intermediate_log', '')}",
        f"ordered_log: {payload.get('ordered_log', '')}",
        f"split_log: {payload.get('split_log', '')}",
        f"finalized_at_utc: {payload.get('updated_at_utc', '')}",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


def consume_new_events(state: BlockerState) -> None:
    if not state.ordered_log.exists():
        return

    with state.ordered_log.open("r", encoding="utf-8", errors="ignore") as f:
        f.seek(state.consumed_bytes)
        while True:
            line = f.readline()
            if not line:
                break
            state.consumed_bytes = f.tell()
            parsed = parse_ordered_line(line)
            if parsed is None:
                continue
            domain, event_time, ips = parsed

            state.total_dns_events += 1
            state.observed_domains.add(domain)

            if is_target_domain(domain, state.target_domains):
                state.youtube_dns_query_seen = True
                if ips:
                    state.youtube_dns_returned = True
                state.youtube_events.append(
                    {
                        "domain": domain,
                        "time": event_time,
                        "ips": ips,
                    }
                )
                state.youtube_ips.update(ips)
            else:
                state.unblocked_domains.add(domain)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Track youtube.com DNS answers and block egress to returned IPs with PF."
    )
    parser.add_argument("--ordered-log", type=Path, required=True)
    parser.add_argument("--split-log", type=Path, default=None)
    parser.add_argument("--target-domain", default="youtube.com")
    parser.add_argument("--run-stamp", default="")
    parser.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR)
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--anchor", default="com.apple/exp3_no_youtube")
    parser.add_argument("--label", default="exp3_youtube_block")
    parser.add_argument("--pid-file", type=Path, default=None)
    return parser.parse_args()


def main() -> int:
    if os.geteuid() != 0:
        print("youtube_ip_blocker.py must run with sudo/root (pfctl requires it).", file=sys.stderr)
        return 1

    args = parse_args()
    stamp = args.run_stamp or run_timestamp()
    args.log_dir.mkdir(parents=True, exist_ok=True)

    state = BlockerState(
        target_domain=args.target_domain.lower().strip("."),
        target_domains=build_target_domains(args.target_domain),
        run_stamp=stamp,
        ordered_log=args.ordered_log,
        split_log=args.split_log,
        intermediate_log=args.log_dir / f"youtube_block_intermediate_{stamp}.json",
        final_summary_json=args.log_dir / f"youtube_block_summary_{stamp}.json",
        final_summary_txt=args.log_dir / f"youtube_block_summary_{stamp}.txt",
        anchor=args.anchor,
        label=args.label,
    )

    if args.pid_file is not None:
        args.pid_file.parent.mkdir(parents=True, exist_ok=True)
        args.pid_file.write_text(str(os.getpid()), encoding="utf-8")

    stop_event = Event()

    def stop_handler(_signum: int, _frame: Any) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)

    pf_ready, last_pf_message = ensure_pf_enabled()
    if not pf_ready:
        last_pf_message = f"PF unavailable: {last_pf_message}"
        print(last_pf_message, file=sys.stderr)

    try:
        while not stop_event.is_set():
            consume_new_events(state)

            if pf_ready and state.youtube_ips != state.blocked_ips:
                state.cumulative_blocked_packets += read_blocked_packet_count(
                    state.anchor, state.label
                )
                ok, msg = apply_anchor_rules(state.anchor, state.youtube_ips, state.label)
                last_pf_message = msg
                if ok:
                    state.blocked_ips = set(state.youtube_ips)
                else:
                    last_pf_message = f"Failed to apply PF rules: {msg}"

            current_packets = (
                read_blocked_packet_count(state.anchor, state.label) if pf_ready else 0
            )
            snapshot = build_snapshot(
                state,
                blocked_packets=state.cumulative_blocked_packets + current_packets,
                pf_ready=pf_ready,
                last_pf_message=last_pf_message,
            )
            write_json(state.intermediate_log, snapshot)
            time.sleep(max(args.poll_seconds, 0.1))
    finally:
        final_packets = (
            state.cumulative_blocked_packets
            + (read_blocked_packet_count(state.anchor, state.label) if pf_ready else 0)
        )
        final_payload = build_snapshot(
            state,
            blocked_packets=final_packets,
            pf_ready=pf_ready,
            last_pf_message=last_pf_message,
        )
        final_payload["intermediate_log"] = str(state.intermediate_log)
        final_payload["final_summary_json"] = str(state.final_summary_json)
        final_payload["final_summary_txt"] = str(state.final_summary_txt)
        final_payload["finalized_at_utc"] = utc_now_iso()

        write_json(state.intermediate_log, final_payload)
        write_json(state.final_summary_json, final_payload)
        write_final_txt(state.final_summary_txt, final_payload)

        if pf_ready:
            clear_anchor_rules(state.anchor)

        if args.pid_file is not None and args.pid_file.exists():
            args.pid_file.unlink(missing_ok=True)

    print(f"Final summary (txt):  {state.final_summary_txt}")
    print(f"Final summary (json): {state.final_summary_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
