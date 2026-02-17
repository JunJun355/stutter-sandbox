# stutter-sandbox

## Experiment 1: IP collision detection

This experiment resolves a list of student-relevant websites repeatedly to spot shared IPs that may collide under CDN/anycast routing.

It now defaults to a single DNS polling pass (`--repetitions 1`) for faster checks.

The output JSON format omits categories to keep files focused on platform and domain.

Every output filename ends with a run timestamp in `YYYYMMDD-HH:MM:SS` format so runs do not overwrite each other.

### Files

- `data/exp1_ip_coll_domains.json`: Input map with `academic`, `entertainment`, and `personal` lists of `{platform, domain}` items.
- `src/exp1_ip_coll_dns_probe.py`: Script that runs repeated DNS lookups and builds reports.
- `log/exp1_ip_coll_all_ips_YYYYMMDD-HH:MM:SS.json`: All resolved IPs per domain.
- `log/exp1_ip_coll_possible_collisions_YYYYMMDD-HH:MM:SS.json`: IPs shared by multiple domains (possible collisions).

### Run

```bash
python3 src/exp1_ip_coll_dns_probe.py
```
