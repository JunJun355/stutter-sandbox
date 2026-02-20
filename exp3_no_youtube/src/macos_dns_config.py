#!/usr/bin/env python3
"""macOS DNS configuration helper for exp3 middleman.

Use this to point network services at 127.0.0.1 so DNS goes through the local proxy.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_LOG_DIR = ROOT_DIR / "log"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H:%M:%S")


def run_cmd(args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        check=check,
        text=True,
        capture_output=True,
    )


def list_network_services() -> list[str]:
    result = run_cmd(["networksetup", "-listallnetworkservices"])
    services: list[str] = []
    for line in result.stdout.splitlines():
        entry = line.strip()
        if not entry:
            continue
        if entry.startswith("An asterisk"):
            continue
        if entry.startswith("*"):
            continue
        services.append(entry)
    return services


def get_dns_servers(service: str) -> list[str]:
    result = run_cmd(["networksetup", "-getdnsservers", service], check=False)
    if result.returncode != 0:
        return []

    out = result.stdout.strip()
    if not out or "There aren't any DNS Servers set on" in out:
        return []

    return [line.strip() for line in out.splitlines() if line.strip()]


def set_dns_servers(service: str, servers: list[str]) -> None:
    if servers:
        run_cmd(["networksetup", "-setdnsservers", service, *servers])
    else:
        run_cmd(["networksetup", "-setdnsservers", service, "Empty"])


def resolve_services(requested: list[str]) -> list[str]:
    if requested:
        return requested
    return list_network_services()


def find_latest_backup(log_dir: Path) -> Path | None:
    backups = list(log_dir.glob("dns_backup_*.json"))
    if not backups:
        return None
    return max(backups, key=lambda p: p.stat().st_mtime)


def get_middleman_status(log_dir: Path) -> tuple[int | None, bool, Path]:
    pid_path = log_dir / "dns_middleman.pid"
    if not pid_path.exists():
        return None, False, pid_path

    try:
        pid = int(pid_path.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return None, False, pid_path

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return pid, False, pid_path
    except PermissionError:
        return pid, True, pid_path
    except OSError:
        return pid, False, pid_path
    return pid, True, pid_path


def cmd_status(services: list[str]) -> int:
    payload = {
        "checked_at_utc": utc_now_iso(),
        "services": [],
    }
    for service in services:
        payload["services"].append(
            {"service": service, "dns_servers": get_dns_servers(service)}
        )
    print(json.dumps(payload, indent=2))
    return 0


def cmd_apply_local(services: list[str], local_dns: str, log_dir: Path) -> int:
    log_dir.mkdir(parents=True, exist_ok=True)
    current = [
        {"service": service, "dns_servers": get_dns_servers(service)}
        for service in services
    ]

    already_local = all(item["dns_servers"] == [local_dns] for item in current)
    if already_local:
        pid, running, pid_path = get_middleman_status(log_dir)
        latest_backup = find_latest_backup(log_dir)
        print("DNS is already configured for the local middleman. No changes applied.")
        if running and pid is not None:
            print(f"Existing setup detected: middleman is running with PID {pid} ({pid_path}).")
        elif pid is not None:
            print(
                f"PID file exists but process is not running (PID {pid}) at {pid_path}."
            )
        else:
            print(f"No middleman PID file found at {pid_path}.")
        if latest_backup is not None:
            print(f"Using existing backup: {latest_backup}")
        else:
            print("No existing backup found in log directory.")
        return 0

    backup_path = log_dir / f"dns_backup_{run_timestamp()}.json"

    backup = {
        "created_at_utc": utc_now_iso(),
        "local_dns": local_dns,
        "services": [],
    }
    backup["services"] = current

    with backup_path.open("w", encoding="utf-8") as f:
        json.dump(backup, f, indent=2)

    for service in services:
        set_dns_servers(service, [local_dns])

    print(f"Applied DNS {local_dns} to services: {', '.join(services)}")
    print(f"Backup written to: {backup_path}")
    return 0


def cmd_restore(backup_path: Path | None, log_dir: Path) -> int:
    if backup_path is None:
        backup_path = find_latest_backup(log_dir)
        if backup_path is None:
            print(
                f"No backup files found in {log_dir}. "
                "Expected files named dns_backup_YYYYMMDD-HH:MM:SS.json.",
                file=sys.stderr,
            )
            return 1
        print(f"No --backup specified. Using most recent backup: {backup_path}")

    if not backup_path.exists():
        print(f"Backup file not found: {backup_path}", file=sys.stderr)
        return 1

    with backup_path.open("r", encoding="utf-8") as f:
        backup = json.load(f)

    services = backup.get("services", [])
    for item in services:
        service = str(item.get("service", "")).strip()
        if not service:
            continue
        dns_servers = item.get("dns_servers", [])
        if not isinstance(dns_servers, list):
            dns_servers = []
        set_dns_servers(service, [str(x) for x in dns_servers])

    print(f"Restored DNS servers from: {backup_path}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="macOS DNS config helper for exp3 middleman")
    sub = parser.add_subparsers(dest="command", required=True)

    status = sub.add_parser("status")
    status.add_argument("--service", action="append", default=[])

    apply_local = sub.add_parser("apply-local")
    apply_local.add_argument("--service", action="append", default=[])
    apply_local.add_argument("--local-dns", default="127.0.0.1")
    apply_local.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR)

    restore = sub.add_parser("restore")
    restore.add_argument("--backup", type=Path, default=None)
    restore.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR)

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.command == "status":
        services = resolve_services(args.service)
        return cmd_status(services)
    if args.command == "apply-local":
        services = resolve_services(args.service)
        return cmd_apply_local(services, args.local_dns, args.log_dir)
    if args.command == "restore":
        return cmd_restore(args.backup, args.log_dir)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
