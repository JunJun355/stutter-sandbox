# stutter-sandbox

## Layout

- `exp1_ip_collisions/`: Experiment 1 (DNS IP collision detection).
- `exp2_dns_middleman/`: Experiment 2 (local DNS middleman and logging).

## Experiment 1: IP collision detection

### Files

- `exp1_ip_collisions/data/domains.json`: Input map with `academic`, `entertainment`, and `personal` lists.
- `exp1_ip_collisions/src/dns_probe.py`: Repeated DNS lookup and collision analysis.
- `exp1_ip_collisions/log/all_ips_YYYYMMDD-HH:MM:SS.json`: Resolved IP report.
- `exp1_ip_collisions/log/possible_collisions_YYYYMMDD-HH:MM:SS.json`: Shared IP report.

### Run

```bash
python3 exp1_ip_collisions/src/dns_probe.py
```

## Experiment 2: DNS middleman

### Files

- `exp2_dns_middleman/data/splits.json`: Current `academic` / `entertainment` / `personal` split lists.
- `exp2_dns_middleman/src/dns_middleman.py`: DNS UDP+TCP forwarder that logs domain access times.
- `exp2_dns_middleman/src/macos_dns_config.py`: macOS DNS apply/restore helper.
- `exp2_dns_middleman/src/run_background.sh`: Start/stop/status background helper.
- `exp2_dns_middleman/log/ordered_domains_YYYYMMDD-HH:MM:SS.json`: Ordered JSON-lines file with one object per line: `{"domain":"...","time":"..."}`.
- `exp2_dns_middleman/log/domain_splits_YYYYMMDD-HH:MM:SS.json`: JSON map: `"domain": ["time1", "time2", ...]`.

### Run foreground

```bash
sudo python3 exp2_dns_middleman/src/dns_middleman.py --listen-port 53
```

### Route macOS DNS through local middleman

```bash
# Check current DNS settings
python3 exp2_dns_middleman/src/macos_dns_config.py status

# Apply 127.0.0.1 DNS to all network services (writes backup file in exp2_dns_middleman/log/)
sudo python3 exp2_dns_middleman/src/macos_dns_config.py apply-local --local-dns 127.0.0.1

# Restore later using the backup path printed by apply-local
sudo python3 exp2_dns_middleman/src/macos_dns_config.py restore --backup <backup_file_path>
```

### Run in background

```bash
# Start (default args)
sudo exp2_dns_middleman/src/run_background.sh start -- --listen-port 53

# Status
sudo exp2_dns_middleman/src/run_background.sh status

# Stop
sudo exp2_dns_middleman/src/run_background.sh stop
```

### Note

Setting DNS to `127.0.0.1` routes standard system DNS through the script. Apps using DNS-over-HTTPS or other custom resolvers can bypass this unless separately disabled or blocked.
