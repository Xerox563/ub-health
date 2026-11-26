# ub-health

ub-health is a single command that checks your Ubuntu computer and tells you what needs attention, in plain English.

It checks:
- Packages waiting to be updated, and security updates
- Whether automatic security updates are turned on
- Whether a restart is needed
- Background services that failed
- Drives that are almost full
- Memory and swap use
- Whether the firewall (ufw) is on
- Error messages in the system log from the last 24 hours
- SSL certificates that are about to expire or have expired

Each problem comes with the command that fixes it. Nothing is changed unless you approve it.

## Requirements

- Ubuntu
- Python 3 (already installed on Ubuntu)
- openssl (already installed on Ubuntu, used for the certificate check)

No extra Python packages are needed.

## Install

```
git clone https://github.com/Xerox563/ub-health.git
cd ub-health
mkdir -p ~/.local/bin
cp ub_health.py ~/.local/bin/ub-health
chmod +x ~/.local/bin/ub-health
```

Make sure `~/.local/bin` is in your PATH. Ubuntu adds it automatically when it exists at login. Open a new terminal, then check:

```
ub-health --version
```

To update later, copy `ub_health.py` to `~/.local/bin/ub-health` again.

## Usage

```
ub-health                 # dashboard with health bar, status cards and charts
ub-health --tui           # full screen interactive view (arrows, f to fix, r to re-run, q to quit)
ub-health --json          # same results as JSON, for scripts
ub-health --report FILE   # save the results as a Markdown report
ub-health --quiet         # print only problems, nothing when all is OK (for cron)
ub-health --refresh       # run apt update first so the update check is current
ub-health --fix           # offer each fix and run it only after you say yes
ub-health --plain         # no colors, good for logs and copy and paste
ub-health --no-history    # do not save this run to the history file
ub-health --help          # list all options
```

In a terminal, the dashboard is colored and shows a health bar, a status card for each check, disk and memory bars, a check mix bar, and a health trend line from earlier runs. Colors turn off automatically when the output is piped or when the `NO_COLOR` environment variable is set.

Some checks need sudo to read everything. If a check cannot read something, it tells you to run `sudo ub-health`.

## Scheduled checks

To run a check every morning and only print when something needs attention, add this to your crontab (`crontab -e`):

```
0 8 * * * /home/YOUR_USER/.local/bin/ub-health --quiet --refresh
```

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Everything is OK |
| 1 | At least one warning |
| 2 | At least one problem that needs action |
| 3 | Not running on Ubuntu |

## Config file

Limits can be changed in `~/.config/ub-health/config.ini`. Any line you leave out keeps its default. Bad values are ignored:

```
[disk]
warn_percent = 80
fail_percent = 90

[memory]
warn_free_percent = 10
fail_free_percent = 5

[certificates]
warn_days = 30
fail_days = 7

[journal]
warn_count = 50
```

## Extra certificates

ub-health checks every certificate in `/etc/letsencrypt/live/`. To check more, create `~/.config/ub-health/certs.txt` and list one certificate path per line. Lines starting with `#` are ignored:

```
# my extra certificates
/srv/myapp/cert.pem
```

## History

Each run is saved to `~/.local/state/ub-health/history.jsonl`. The dashboard uses the last runs to draw the health trend. Use `--no-history` to skip saving.

## Running the tests

```
python3 -m unittest discover -s tests
```

The tests use fake system conditions, so they do not change anything on your computer.

## Default thresholds

| Check | Warning | Problem |
|---|---|---|
| Disk | 80% full | 90% full |
| Memory | under 10% free | under 5% free |
| Certificate | expires in under 30 days | expires in under 7 days, or already expired |
| System log | 50 or more errors in 24 hours | none |
| Security updates | none | any waiting |
| Automatic security updates | off | none |
| Restart | none | needed |
| Failed services | none | any failed |
| Firewall | off | none |
