# exp2_dns_middleman

## Purpose

Run a local DNS middleman on macOS that:

- Receives DNS queries on a local listener.
- Forwards to an upstream resolver.
- Logs domain access time events and resolved IPs.

This enables DNS-path observability for later policy experiments.

## Directory layout

- `data/splits.json`: Domain split input (`academic`, `entertainment`, `personal`).
- `src/dns_middleman.py`: DNS forwarder/logger.
- `src/macos_dns_config.py`: Apply/restore DNS servers on macOS services.
- `src/run_background.sh`: Background process helper.
- `log/`: Runtime logs, backups, PID/stdout files.

## Output files

Per middleman run:

- `ordered_domains_YYYYMMDD-HH:MM:SS.txt`  
  Plain text, one event per line:
  `[instagram.com] @ [20260220-11:20:01] using [157.240.22.174]`
- `domain_splits_YYYYMMDD-HH:MM:SS.json`  
  JSON map:
  `"instagram.com": ["20260220-11:20:01", "20260220-11:21:10"]`

Per DNS apply action:

- `dns_backup_YYYYMMDD-HH:MM:SS.json`  
  Backup of original DNS settings by network service.

Background helper artifacts:

- `dns_middleman.pid`
- `dns_middleman_stdout_YYYYMMDD-HH:MM:SS.log`

## Typical workflow (macOS)

1) Check current DNS service config:

```bash
python3 src/macos_dns_config.py status
```

2) Start middleman in background:

```bash
sudo src/run_background.sh start -- --listen-port 53
```

3) Route system DNS to local listener:

```bash
sudo python3 src/macos_dns_config.py apply-local --local-dns 127.0.0.1
```

4) Verify running status and logs:

```bash
sudo src/run_background.sh status
ls -lt log
```

5) Stop and restore DNS:

```bash
sudo src/run_background.sh stop
sudo python3 src/macos_dns_config.py restore
```

Optional: restore from a specific backup file instead of latest:

```bash
sudo python3 src/macos_dns_config.py restore --backup log/dns_backup_YYYYMMDD-HH:MM:SS.json
```

## Direct foreground run

```bash
sudo python3 src/dns_middleman.py \
  --listen-host 127.0.0.1 \
  --listen-port 53 \
  --upstream-host 1.1.1.1 \
  --upstream-port 53
```

Optional finite run:

```bash
sudo python3 src/dns_middleman.py \
  --listen-port 53 \
  --duration-seconds 120
```

## CLI options (dns_middleman.py)

- `--listen-host`: Local bind host (default `127.0.0.1`).
- `--listen-port`: Local bind port (default `53`).
- `--upstream-host`: Upstream resolver (default `1.1.1.1`).
- `--upstream-port`: Upstream resolver port (default `53`).
- `--timeout-seconds`: Upstream timeout (default `3.0`).
- `--log-dir`: Output directory.
- `--splits`: Split map path.
- `--duration-seconds`: Stop after N seconds (`0` means run until interrupted).

## CLI options (macos_dns_config.py)

Commands:

- `status [--service <service>]...`
- `apply-local [--service <service>]... [--local-dns 127.0.0.1] [--log-dir <dir>]`
- `restore [--backup <backup_file>] [--log-dir <dir>]`

## Limitations

- This captures standard DNS on port 53 routed through system resolver settings.
- Apps/browsers using DNS-over-HTTPS or custom in-app resolvers can bypass this path.
- Binding port 53 requires elevated privileges.
- `apply-local` is idempotent: if DNS is already set to local middleman, it does not create another backup and reports existing PID/backup info.
