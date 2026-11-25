#!/usr/bin/env python3
"""ub-health: a plain English health check for Ubuntu."""

import argparse
import datetime
import glob
import json
import os
import shutil
import subprocess
from dataclasses import asdict, dataclass, field

VERSION = "0.1.0"

OK = "OK"
WARN = "WARN"
FAIL = "FAIL"


@dataclass
class Result:
    """The outcome of one check."""

    name: str
    status: str
    message: str
    fix: str = ""
    fix_command: list = field(default_factory=list)


def parse_upgradable(text):
    """Return the package names from `apt list --upgradable` output."""
    names = []
    for line in text.splitlines():
        if "[upgradable" in line and "/" in line:
            names.append(line.split("/", 1)[0])
    return names


def parse_security(text):
    """Return the names of upgradable packages that come from security updates."""
    names = []
    for line in text.splitlines():
        if "[upgradable" in line and "-security" in line.split("/", 1)[-1]:
            names.append(line.split("/", 1)[0])
    return names


def refresh_package_lists(run=subprocess.run):
    """Run `apt update` so the upgrade list is current. Asks for sudo if needed."""
    run(["sudo", "apt", "update"])


def check_updates(apt_output=None, refresh=False):
    """Report how many packages can be upgraded, flagging security updates."""
    if refresh:
        refresh_package_lists()
    if apt_output is None:
        apt_output = subprocess.run(
            ["apt", "list", "--upgradable"],
            capture_output=True, text=True,
        ).stdout
    count = len(parse_upgradable(apt_output))
    security = len(parse_security(apt_output))
    fix = "sudo apt update && sudo apt upgrade"
    if security:
        return Result(
            "Updates", FAIL,
            f"{security} security update(s) waiting ({count} packages in total)",
            fix=fix,
        )
    if count:
        return Result("Updates", WARN, f"{count} packages can be upgraded", fix=fix)
    return Result("Updates", OK, "Everything is up to date")


REBOOT_FLAG = "/var/run/reboot-required"
REBOOT_PACKAGES = "/var/run/reboot-required.pkgs"


def check_reboot(flag_path=REBOOT_FLAG, packages_path=REBOOT_PACKAGES):
    """Report whether a restart is needed after an update."""
    if not os.path.exists(flag_path):
        return Result("Reboot", OK, "No restart needed")
    message = "A restart is needed to finish installing updates"
    try:
        with open(packages_path) as handle:
            packages = sorted({line.strip() for line in handle if line.strip()})
        if packages:
            message += f" (for {', '.join(packages[:3])})"
    except OSError:
        pass
    return Result("Reboot", FAIL, message, fix="sudo reboot")


def parse_failed_services(text):
    """Return the unit names from `systemctl --failed --plain` output."""
    names = []
    for line in text.splitlines():
        parts = line.split()
        if parts and parts[0].endswith(".service"):
            names.append(parts[0])
    return names


def check_services(systemctl_output=None):
    """Report background services that have failed."""
    if systemctl_output is None:
        try:
            systemctl_output = subprocess.run(
                ["systemctl", "--failed", "--no-legend", "--plain"],
                capture_output=True, text=True,
            ).stdout
        except FileNotFoundError:
            return Result("Services", WARN, "Could not check services (systemctl not found)")
    failed = parse_failed_services(systemctl_output)
    if not failed:
        return Result("Services", OK, "No failed services")
    return Result(
        "Services", FAIL,
        f"{len(failed)} failed: {', '.join(failed[:3])}",
        fix=f"systemctl status {failed[0]}",
    )


REAL_FILESYSTEMS = {"ext2", "ext3", "ext4", "xfs", "btrfs", "f2fs", "vfat", "zfs"}
DISK_WARN_PERCENT = 80
DISK_FAIL_PERCENT = 90


def parse_real_mounts(text):
    """Return mount points of real drives from /proc/mounts style text."""
    mounts = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 3 and parts[2] in REAL_FILESYSTEMS and parts[1] not in mounts:
            mounts.append(parts[1])
    return mounts


def check_disk(mounts_text=None, usage=shutil.disk_usage):
    """Report the fullest real drive, warning when it is getting full."""
    if mounts_text is None:
        try:
            with open("/proc/mounts") as handle:
                mounts_text = handle.read()
        except OSError:
            return Result("Disk", WARN, "Could not read the list of drives")
    worst = None
    for mount in parse_real_mounts(mounts_text):
        try:
            total, used, free = usage(mount)
        except OSError:
            continue
        if not total:
            continue
        percent = used * 100 // total
        if worst is None or percent > worst[1]:
            worst = (mount, percent, free)
    if worst is None:
        return Result("Disk", WARN, "Could not check any drives")
    mount, percent, free = worst
    free_gb = free / 1_000_000_000
    message = f"{mount} is {percent}% full ({free_gb:.1f} GB free)"
    fix = "sudo apt autoremove && sudo apt clean"
    if percent >= DISK_FAIL_PERCENT:
        return Result("Disk", FAIL, message, fix=fix)
    if percent >= DISK_WARN_PERCENT:
        return Result("Disk", WARN, message, fix=fix)
    return Result("Disk", OK, message)


LETSENCRYPT_GLOB = "/etc/letsencrypt/live/*/cert.pem"
CERT_WARN_DAYS = 30
CERT_FAIL_DAYS = 7


CONFIG_PATH = os.path.expanduser("~/.config/ub-health/certs.txt")


def load_extra_cert_paths(config_path=CONFIG_PATH):
    """Read extra certificate paths, one per line. Lines starting with # are ignored."""
    try:
        with open(config_path) as handle:
            lines = handle.read().splitlines()
    except OSError:
        return []
    return [line.strip() for line in lines
            if line.strip() and not line.strip().startswith("#")]


def read_expiry(path):
    """Return the expiry date of a certificate file, as a UTC datetime."""
    output = subprocess.run(
        ["openssl", "x509", "-in", path, "-noout", "-enddate"],
        capture_output=True, text=True, check=True,
    ).stdout
    # Output looks like: notAfter=Mar 12 08:00:00 2027 GMT
    stamp = output.strip().split("=", 1)[1]
    return datetime.datetime.strptime(stamp, "%b %d %H:%M:%S %Y %Z").replace(
        tzinfo=datetime.timezone.utc
    )


def check_certificates(paths=None, expiry=read_expiry, now=None):
    """Report certificates that expire soon or have already expired."""
    if paths is None:
        paths = sorted(glob.glob(LETSENCRYPT_GLOB)) + load_extra_cert_paths()
    if not paths:
        return Result("Certificates", OK, "No certificates found to check")
    now = now or datetime.datetime.now(datetime.timezone.utc)
    soonest = None
    unreadable = 0
    for path in paths:
        try:
            expires = expiry(path)
        except (OSError, subprocess.CalledProcessError):
            unreadable += 1
            continue
        if soonest is None or expires < soonest[1]:
            soonest = (path, expires)
    if soonest is None:
        return Result("Certificates", WARN,
                      f"Could not read {unreadable} certificate(s) (run with sudo)",
                      fix="sudo ub-health")
    path, expires = soonest
    days = (expires - now).days
    note = f", {unreadable} could not be read (run with sudo)" if unreadable else ""
    if days < 0:
        message = f"{path} has already expired{note}"
        return Result("Certificates", FAIL, message, fix="sudo certbot renew")
    message = f"{path} expires in {days} days{note}"
    fix = "sudo certbot renew"
    if days < CERT_FAIL_DAYS:
        return Result("Certificates", FAIL, message, fix=fix)
    if days < CERT_WARN_DAYS:
        return Result("Certificates", WARN, message, fix=fix)
    return Result("Certificates", OK, f"Soonest expiry is in {days} days{note}")


def render_report(results, today=None):
    """Turn a list of results into the one screen text report."""
    today = today or datetime.date.today()
    lines = [f"Ubuntu Health Check, {today:%d %b %Y}", ""]
    for result in results:
        lines.append(f"[{result.status:<4}] {result.name}: {result.message}")
        if result.fix and result.status != OK:
            lines.append(f"       Fix: {result.fix}")
    lines.append("")
    problems = sum(1 for r in results if r.status != OK)
    if problems:
        lines.append(f"{problems} item(s) need attention.")
    else:
        lines.append("Everything looks good.")
    return "\n".join(lines)


def exit_code(results):
    """0 when all OK, 1 when there are warnings, 2 when there are failures."""
    statuses = {r.status for r in results}
    if FAIL in statuses:
        return 2
    if WARN in statuses:
        return 1
    return 0


def is_ubuntu(os_release_text=None):
    """Return True when /etc/os-release says this is Ubuntu."""
    if os_release_text is None:
        try:
            with open("/etc/os-release") as handle:
                os_release_text = handle.read()
        except OSError:
            return False
    for line in os_release_text.splitlines():
        if line.startswith("ID="):
            return line.split("=", 1)[1].strip().strip('"') == "ubuntu"
    return False


def run_fixes(results, ask=input, run=subprocess.run):
    """Offer each suggested fix and run it only after a clear yes."""
    offered = set()
    for result in results:
        if result.status == OK or not result.fix or result.fix in offered:
            continue
        offered.add(result.fix)
        print(f"\nFix for {result.name}: {result.fix}")
        try:
            answer = ask("Run this fix now? [y/N] ").strip().lower()
        except EOFError:
            answer = ""
        if answer in ("y", "yes"):
            run(result.fix, shell=True)
        else:
            print("Skipped.")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="ub-health",
        description="Check your Ubuntu computer and explain what needs attention.",
    )
    parser.add_argument(
        "--version", action="version", version=f"ub-health {VERSION}"
    )
    parser.add_argument(
        "--json", action="store_true",
        help="print the results as JSON for scripts",
    )
    parser.add_argument(
        "--refresh", action="store_true",
        help="run apt update first so the update check is current",
    )
    parser.add_argument(
        "--fix", action="store_true",
        help="offer each fix and run it only after you say yes",
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    if not is_ubuntu():
        print("ub-health only works on Ubuntu. This system does not look like Ubuntu.")
        return 3
    results = [check_updates(refresh=args.refresh), check_reboot(), check_services(),
               check_disk(),
               check_certificates()]
    if args.json:
        print(json.dumps([asdict(r) for r in results], indent=2))
    else:
        print(render_report(results))
    if args.fix:
        run_fixes(results)
    return exit_code(results)


if __name__ == "__main__":
    raise SystemExit(main())
