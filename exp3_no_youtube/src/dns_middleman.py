#!/usr/bin/env python3
"""Exp3 DNS middleman.

Forwards DNS traffic to an upstream resolver and logs domain access times.
Outputs:
1) ordered domain-time events (plain text, one line per event)
2) domain -> [times] JSON map
"""

from __future__ import annotations

import argparse
import json
import signal
import socket
import struct
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_LOG_DIR = ROOT_DIR / "log"
DEFAULT_SPLITS = ROOT_DIR / "data" / "splits.json"


def run_timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H:%M:%S")


def event_timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H:%M:%S")


def decode_dns_name(data: bytes, offset: int) -> tuple[str, int]:
    labels: list[str] = []
    cursor = offset
    end_offset = offset
    jumped = False
    seen_pointers: set[int] = set()

    while True:
        if cursor >= len(data):
            break

        length = data[cursor]
        if length == 0:
            cursor += 1
            if not jumped:
                end_offset = cursor
            break

        # Compression pointer: two bytes with top two bits set.
        if length & 0xC0 == 0xC0:
            if cursor + 1 >= len(data):
                break
            pointer = ((length & 0x3F) << 8) | data[cursor + 1]
            if pointer in seen_pointers:
                break
            seen_pointers.add(pointer)
            if not jumped:
                end_offset = cursor + 2
            cursor = pointer
            jumped = True
            continue

        if length & 0xC0:
            break

        cursor += 1
        if cursor + length > len(data):
            break
        label = data[cursor : cursor + length].decode("utf-8", errors="ignore")
        labels.append(label)
        cursor += length
        if not jumped:
            end_offset = cursor

    name = ".".join(part for part in labels if part).strip(".").lower()
    if end_offset <= offset:
        end_offset = cursor
    return name, end_offset


def extract_query_domain(payload: bytes) -> str | None:
    if len(payload) < 12:
        return None
    qdcount = struct.unpack("!H", payload[4:6])[0]
    if qdcount == 0:
        return None
    domain, _ = decode_dns_name(payload, 12)
    return domain or None


def extract_answer_ips(payload: bytes) -> list[str]:
    if len(payload) < 12:
        return []

    qdcount = struct.unpack("!H", payload[4:6])[0]
    ancount = struct.unpack("!H", payload[6:8])[0]
    offset = 12

    for _ in range(qdcount):
        _, offset = decode_dns_name(payload, offset)
        if offset + 4 > len(payload):
            return []
        offset += 4

    ips: set[str] = set()
    for _ in range(ancount):
        _, offset = decode_dns_name(payload, offset)
        if offset + 10 > len(payload):
            break

        rtype, rclass, _ttl, rdlen = struct.unpack("!HHIH", payload[offset : offset + 10])
        offset += 10
        if offset + rdlen > len(payload):
            break

        rdata = payload[offset : offset + rdlen]
        offset += rdlen

        if rclass != 1:
            continue
        if rtype == 1 and rdlen == 4:
            ips.add(socket.inet_ntoa(rdata))
        elif rtype == 28 and rdlen == 16:
            ips.add(socket.inet_ntop(socket.AF_INET6, rdata))

    return sorted(ips)


def make_servfail(query: bytes) -> bytes | None:
    if len(query) < 12:
        return None
    qdcount = query[4:6]
    header = query[:2] + b"\x81\x82" + qdcount + b"\x00\x00\x00\x00\x00\x00"
    return header + query[12:]


def recv_exact(sock: socket.socket, size: int) -> bytes | None:
    buf = bytearray()
    while len(buf) < size:
        chunk = sock.recv(size - len(buf))
        if not chunk:
            return None
        buf.extend(chunk)
    return bytes(buf)


class DNSMiddleman:
    def __init__(
        self,
        listen_host: str,
        listen_port: int,
        upstream_host: str,
        upstream_port: int,
        timeout_seconds: float,
        log_dir: Path,
        run_stamp: str,
    ) -> None:
        self.listen_host = listen_host
        self.listen_port = listen_port
        self.upstream_host = upstream_host
        self.upstream_port = upstream_port
        self.timeout_seconds = timeout_seconds
        self.log_dir = log_dir
        self.run_stamp = run_stamp

        self.stop_event = threading.Event()
        self.lock = threading.Lock()
        self.domain_times: dict[str, list[str]] = defaultdict(list)
        self.domain_ips: dict[str, set[str]] = defaultdict(set)
        self.query_count = 0

        self.ordered_path = (
            self.log_dir / f"ordered_domains_{self.run_stamp}.txt"
        )
        self.split_path = (
            self.log_dir / f"domain_splits_{self.run_stamp}.json"
        )

        self.ordered_fp = None

    def start(self, duration_seconds: int = 0) -> None:
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.ordered_fp = self.ordered_path.open("w", encoding="utf-8")

        udp_thread = threading.Thread(target=self.udp_loop, name="dns-udp", daemon=True)
        tcp_thread = threading.Thread(target=self.tcp_loop, name="dns-tcp", daemon=True)
        udp_thread.start()
        tcp_thread.start()

        start_time = time.monotonic()
        try:
            while not self.stop_event.is_set():
                time.sleep(0.2)
                if duration_seconds > 0 and (time.monotonic() - start_time) >= duration_seconds:
                    self.stop_event.set()
        finally:
            self.flush_outputs()
            if self.ordered_fp is not None:
                self.ordered_fp.close()

    def record_event(self, domain: str, ips: list[str]) -> None:
        ts = event_timestamp()
        ips_text = ", ".join(ips)
        line = f"[{domain}] @ [{ts}] using [{ips_text}]"
        with self.lock:
            if self.ordered_fp is not None:
                self.ordered_fp.write(line + "\n")
                self.ordered_fp.flush()
            self.domain_times[domain].append(ts)
            for ip in ips:
                self.domain_ips[domain].add(ip)
            self.query_count += 1

    def flush_outputs(self) -> None:
        with self.lock:
            payload = {domain: times for domain, times in sorted(self.domain_times.items())}

        with self.split_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=False)

    def forward_udp(self, query: bytes) -> bytes | None:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as upstream:
                upstream.settimeout(self.timeout_seconds)
                upstream.sendto(query, (self.upstream_host, self.upstream_port))
                response, _ = upstream.recvfrom(65535)
                return response
        except OSError:
            return None

    def forward_tcp(self, query: bytes) -> bytes | None:
        try:
            with socket.create_connection(
                (self.upstream_host, self.upstream_port), timeout=self.timeout_seconds
            ) as upstream:
                upstream.settimeout(self.timeout_seconds)
                upstream.sendall(struct.pack("!H", len(query)) + query)
                raw_len = recv_exact(upstream, 2)
                if raw_len is None:
                    return None
                response_len = struct.unpack("!H", raw_len)[0]
                response = recv_exact(upstream, response_len)
                return response
        except OSError:
            return None

    def udp_loop(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as server:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                server.bind((self.listen_host, self.listen_port))
            except OSError as exc:
                print(
                    f"UDP bind failed on {self.listen_host}:{self.listen_port}: {exc}. "
                    "Run with sudo and ensure the port is free."
                )
                self.stop_event.set()
                return
            server.settimeout(1.0)

            while not self.stop_event.is_set():
                try:
                    query, client_addr = server.recvfrom(65535)
                except socket.timeout:
                    continue
                except OSError:
                    continue

                domain = extract_query_domain(query) or "unknown"
                response = self.forward_udp(query)
                if response is None:
                    response = make_servfail(query)
                if response is None:
                    continue

                try:
                    server.sendto(response, client_addr)
                except OSError:
                    continue

                ips = extract_answer_ips(response)
                self.record_event(domain, ips)

    def handle_tcp_client(self, conn: socket.socket) -> None:
        with conn:
            conn.settimeout(1.0)
            while not self.stop_event.is_set():
                try:
                    raw_len = recv_exact(conn, 2)
                except socket.timeout:
                    continue
                except OSError:
                    return

                if raw_len is None:
                    return
                query_len = struct.unpack("!H", raw_len)[0]
                query = recv_exact(conn, query_len)
                if query is None:
                    return

                domain = extract_query_domain(query) or "unknown"
                response = self.forward_tcp(query)
                if response is None:
                    response = make_servfail(query)
                if response is None:
                    continue

                try:
                    conn.sendall(struct.pack("!H", len(response)) + response)
                except OSError:
                    return

                ips = extract_answer_ips(response)
                self.record_event(domain, ips)

    def tcp_loop(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                server.bind((self.listen_host, self.listen_port))
            except OSError as exc:
                print(
                    f"TCP bind failed on {self.listen_host}:{self.listen_port}: {exc}. "
                    "Run with sudo and ensure the port is free."
                )
                self.stop_event.set()
                return
            server.listen(128)
            server.settimeout(1.0)

            while not self.stop_event.is_set():
                try:
                    conn, _addr = server.accept()
                except socket.timeout:
                    continue
                except OSError:
                    continue

                client_thread = threading.Thread(
                    target=self.handle_tcp_client,
                    args=(conn,),
                    name="dns-tcp-client",
                    daemon=True,
                )
                client_thread.start()


def load_known_domains(splits_path: Path) -> set[str]:
    if not splits_path.exists():
        return set()
    with splits_path.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    known: set[str] = set()
    if not isinstance(raw, dict):
        return known
    for key in ("academic", "entertainment", "personal"):
        entries = raw.get(key, [])
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            domain = str(entry.get("domain", "")).strip().lower()
            if domain:
                known.add(domain)
    return known


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run exp3 DNS middleman and log domain access.")
    parser.add_argument("--listen-host", default="127.0.0.1")
    parser.add_argument("--listen-port", type=int, default=53)
    parser.add_argument("--upstream-host", default="1.1.1.1")
    parser.add_argument("--upstream-port", type=int, default=53)
    parser.add_argument("--timeout-seconds", type=float, default=3.0)
    parser.add_argument("--log-dir", type=Path, default=DEFAULT_LOG_DIR)
    parser.add_argument("--splits", type=Path, default=DEFAULT_SPLITS)
    parser.add_argument("--duration-seconds", type=int, default=0)
    parser.add_argument(
        "--run-stamp",
        default="",
        help="Optional fixed run stamp for output file names.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    stamp = args.run_stamp or run_timestamp()
    known_domains = load_known_domains(args.splits)
    if known_domains:
        print(f"Loaded {len(known_domains)} split domains from {args.splits}")

    middleman = DNSMiddleman(
        listen_host=args.listen_host,
        listen_port=args.listen_port,
        upstream_host=args.upstream_host,
        upstream_port=args.upstream_port,
        timeout_seconds=args.timeout_seconds,
        log_dir=args.log_dir,
        run_stamp=stamp,
    )

    def stop_handler(_signum: int, _frame: Any) -> None:
        middleman.stop_event.set()

    signal.signal(signal.SIGINT, stop_handler)
    signal.signal(signal.SIGTERM, stop_handler)

    print(
        "Starting DNS middleman on "
        f"{args.listen_host}:{args.listen_port} -> {args.upstream_host}:{args.upstream_port}"
    )
    print(f"Ordered output: {middleman.ordered_path}")
    print(f"Split output:   {middleman.split_path}")
    middleman.start(duration_seconds=args.duration_seconds)
    print(f"Stopped. Total captured DNS events: {middleman.query_count}")


if __name__ == "__main__":
    main()
