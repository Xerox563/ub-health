# ub-health

ub-health is a single command that checks your Ubuntu computer and tells you what needs attention, in plain English.

It checks:
- Packages waiting to be updated, and security updates
- Whether a restart is needed
- Background services that failed
- Drives that are almost full
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

## Usage

```
ub-health            # full one-screen report
ub-health --json     # same results as JSON, for scripts
ub-health --refresh  # run apt update first so the update check is current
ub-health --fix      # offer each fix and run it only after you say yes
ub-health --help     # list all options
```

Some checks need sudo to read everything. If a check cannot read something, it tells you to run `sudo ub-health`.

## Exit codes

| Code | Meaning |
|---|---|
| 0 | Everything is OK |
| 1 | At least one warning |
| 2 | At least one problem that needs action |
| 3 | Not running on Ubuntu |

## Certificates

ub-health checks every certificate in `/etc/letsencrypt/live/`. To check more, create the file `~/.config/ub-health/certs.txt` and list one certificate path per line. Lines starting with `#` are ignored:

```
# my extra certificates
/srv/myapp/cert.pem
```

## Running the tests

```
python3 -m unittest discover -s tests
```

The tests use fake system conditions, so they do not change anything on your computer.

## Thresholds

| Check | Warning | Problem |
|---|---|---|
| Disk | 80% full | 90% full |
| Certificate | expires in under 30 days | expires in under 7 days, or already expired |
| Security updates | none | any waiting |
| Restart | none | needed |
| Failed services | none | any failed |
