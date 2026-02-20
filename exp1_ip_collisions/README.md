# exp1_ip_collisions

## Purpose

Measure DNS answer overlap across many domains and identify possible shared-edge IP collisions (for example under CDN/anycast).

This experiment is measurement-only. It does not enforce traffic policy.

## Directory layout

- `data/domains.json`: Input domain set.
- `src/dns_probe.py`: Probe and report script.
- `log/`: Timestamped reports.

## Input format

`domains.json` supports either:

- A list of objects with `platform` and `domain`.
- A map with keys `academic`, `entertainment`, `personal`, each containing lists of `{ "platform": "...", "domain": "..." }`.

Domains are normalized to lowercase and deduplicated.

## Outputs

Script writes two log files per run:

- `all_ips_YYYYMMDD-HH:MM:SS.json`: Per-domain resolved IPs and stability fields.
- `possible_collisions_YYYYMMDD-HH:MM:SS.json`: IPs shared by multiple domains.

## Run

Default run (single poll):

```bash
python3 src/dns_probe.py
```

Multi-poll example:

```bash
python3 src/dns_probe.py \
  --repetitions 5 \
  --wait-seconds 60
```

Custom output file paths:

```bash
python3 src/dns_probe.py \
  --all-ips-output /tmp/all_ips.json \
  --collisions-output /tmp/possible_collisions.json
```

## CLI options

- `--input`: Input JSON path (default: `data/domains.json`).
- `--repetitions`: Number of DNS polling rounds (default: `1`).
- `--wait-seconds`: Delay between rounds (default: `60`).
- `--all-ips-output`: Optional explicit output path.
- `--collisions-output`: Optional explicit output path.

## Notes

- Results depend on resolver behavior and current routing.
- IP overlap across unrelated domains is normal on shared CDN edges.
