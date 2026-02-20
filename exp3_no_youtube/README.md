# exp3_no_youtube

## Purpose

Run a local DNS middleman and a YouTube-specific IP blocker on macOS.

Behavior:

- DNS for all domains is allowed and logged by `src/dns_middleman.py`.
- Only IPs returned for `youtube.com` and `www.youtube.com` are blocked at egress using PF.
- Browser traffic is generated via `src/browser.py` with DoH-related flags disabled.
- A continuously updated intermediate JSON log is written during the run.
- A final summary log is written at close.

## Directory layout

- `src/dns_middleman.py`: DNS forwarder/logger copied from experiment 2.
- `src/macos_dns_config.py`: macOS DNS apply/restore helper copied from experiment 2.
- `src/run_background.sh`: background launcher for DNS middleman.
- `src/browser.py`: Playwright Chromium launcher.
- `src/youtube_ip_blocker.py`: Parses ordered DNS log and installs PF block rules for YouTube IPs.
- `src/init1.sh`: Experiment setup and dependency checks.
- `src/run1.sh`: Start experiment end-to-end, open `youtube.com`, wait for Enter to close.
- `src/close1.sh`: Stop processes, restore DNS, clear PF rules, and finalize logs.
- `data/splits.json`: Optional split map copied from experiment 2.
- `data/browser_profile/`: Persistent Chromium profile.
- `log/`: Runtime logs and state files.

## Prerequisites

Install Playwright and Chromium:

```bash
python3 -m pip install playwright
python3 -m playwright install chromium
```

macOS commands required:

- `networksetup`
- `pfctl`
- `sudo` access (for DNS changes, port 53 listener, PF updates)

## One-time setup

```bash
src/init1.sh
```

`init1.sh` will:

- ensure `data/` and `log/` exist
- copy `data/splits.json` from experiment 2 if missing
- set execute permissions on exp3 scripts
- verify Playwright is importable

## Run experiment

```bash
src/run1.sh
```

`run1.sh` does the following:

1) starts the DNS middleman on port 53 (`sudo`)
2) points macOS DNS to `127.0.0.1` (`sudo`)
3) starts `youtube_ip_blocker.py` (`sudo`)
4) primes DNS for `youtube.com` and `www.youtube.com`, then waits until blocker is armed
5) starts the local Playwright browser and opens `https://youtube.com`
6) waits for Enter keypress
7) calls `src/close1.sh` for graceful shutdown

To stop manually from another terminal:

```bash
src/close1.sh
```

## Logs and outputs

Per run stamp `YYYYMMDD-HH:MM:SS`:

- `log/ordered_domains_<stamp>.txt`  
  DNS events:
  `[domain] @ [time] using [ip1, ip2, ...]`

- `log/domain_splits_<stamp>.json`  
  Domain -> list of event timestamps map.

- `log/youtube_block_intermediate_<stamp>.json`  
  Continuously updated state including:
  - whether `youtube.com` DNS query was seen
  - whether a non-empty YouTube DNS answer was returned
  - observed domains and unblocked-domain list
  - current blocked YouTube IPs
  - blocked packet counter so far

- `log/youtube_block_summary_<stamp>.txt`  
  Final human-readable summary created at close, including:
  - `youtube_dns_returned`
  - `unblocked_domains`
  - `blocked_youtube_outgoing_packets`

- `log/youtube_block_summary_<stamp>.json`  
  Same final data in structured JSON form.

- `log/run1_state_<stamp>.json` and `log/run1_active.json`  
  Run/session state used by `close1.sh`.

## Graceful close behavior

`src/close1.sh`:

1) stops browser process
2) signals blocker process so it writes final summary
3) stops DNS middleman
4) restores DNS settings from latest backup
5) clears exp3 PF anchor rules
6) clears active run marker

## Browser-only command (optional)

If you only want to test the browser with DoH-related flags disabled:

```bash
python3 src/browser.py --url https://youtube.com
```
