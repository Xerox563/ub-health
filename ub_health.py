#!/usr/bin/env python3
"""ub-health: a plain English health check for Ubuntu."""

import argparse
import datetime
import glob
import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field

VERSION = "0.1.0"

OK = "OK"
WARN = "WARN"
FAIL = "FAIL"

ICONS = {OK: "✔", WARN: "⚠", FAIL: "✖"}
COLORS = {OK: "32", WARN: "33", FAIL: "31"}


def paint(text, *codes, enabled=False):
    """Wrap text in terminal color codes, only when colors are turned on."""
    if not enabled or not codes:
        return text
    return f"\033[{';'.join(codes)}m{text}\033[0m"


@dataclass
class Result:
    """The outcome of one check."""

    name: str
    status: str
    message: str
    fix: str = ""
    fix_command: list = field(default_factory=list)
    # Rows for charts, as (label, percent used, note). Empty when there is nothing to chart.
    details: list = field(default_factory=list)


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
    """Return mount points of real drives from /proc/mounts style text.

    A drive can be mounted in more than one place (for example a Snap bind mount),
    so each device is listed only once, under its first mount point.
    """
    mounts = []
    seen_devices = set()
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 3 or parts[2] not in REAL_FILESYSTEMS:
            continue
        device, mount = parts[0], parts[1]
        if device in seen_devices:
            continue
        seen_devices.add(device)
        mounts.append(mount)
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
    details = []
    for mount in parse_real_mounts(mounts_text):
        try:
            total, used, free = usage(mount)
        except OSError:
            continue
        if not total:
            continue
        percent = used * 100 // total
        details.append((mount, percent, f"{free / 1_000_000_000:.1f} GB free"))
        if worst is None or percent > worst[1]:
            worst = (mount, percent, free)
    if worst is None:
        return Result("Disk", WARN, "Could not check any drives")
    mount, percent, free = worst
    free_gb = free / 1_000_000_000
    message = f"{mount} is {percent}% full ({free_gb:.1f} GB free)"
    fix = "sudo apt autoremove && sudo apt clean"
    if percent >= DISK_FAIL_PERCENT:
        return Result("Disk", FAIL, message, fix=fix, details=details)
    if percent >= DISK_WARN_PERCENT:
        return Result("Disk", WARN, message, fix=fix, details=details)
    return Result("Disk", OK, message, details=details)


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


WIDTH = 64


def level_code(percent):
    """Green when fine, yellow when getting full, red when critical."""
    if percent >= DISK_FAIL_PERCENT:
        return "31"
    if percent >= DISK_WARN_PERCENT:
        return "33"
    return "32"


def render_report(results, today=None, color=False):
    """Build the one screen dashboard: a header, a health bar and a status card per check."""
    today = today or datetime.date.today()
    inner = WIDTH - 2
    title = f"Ubuntu Health Check  ·  {today:%d %b %Y}"
    lines = [
        paint("╭" + "─" * inner + "╮", "36", enabled=color),
        paint("│", "36", enabled=color) + " " + paint(title, "1", enabled=color)
        + " " * (inner - 1 - len(title)) + paint("│", "36", enabled=color),
        paint("╰" + "─" * inner + "╯", "36", enabled=color),
        "",
    ]

    total = len(results)
    ok_count = sum(1 for r in results if r.status == OK)
    health = ok_count * 100 // total if total else 100
    health_code = "32" if health >= 80 else "33" if health >= 50 else "31"
    lines.append(f"  Health  {bar(health, 30, health_code, color)}  {health}%")
    lines.append(paint(f"          {ok_count} of {total} checks are OK", "2", enabled=color))
    lines.append("")

    for result in results:
        icon = paint(ICONS[result.status], COLORS[result.status], "1", enabled=color)
        name = paint(f"{result.name:<13}", "1", enabled=color)
        lines.append(f"  {icon} {name} {result.message}")
        if result.fix and result.status != OK:
            lines.append("      " + paint(f"→ Fix: {result.fix}", "2", enabled=color))
    lines.append("")

    disk = next((r for r in results if r.name == "Disk" and r.details), None)
    if disk:
        lines.append("  " + paint("Disk usage", "1", enabled=color))
        for label, percent, note in disk.details:
            lines.append(f"    {label:<12} {bar(percent, 24, level_code(percent), color)}"
                         f"  {percent:>3}%  {note}")
        lines.append("")

    counts = {s: sum(1 for r in results if r.status == s) for s in (OK, WARN, FAIL)}
    lines.append("  " + paint("Check mix", "1", enabled=color))
    lines.append("    " + stacked_bar(counts, 24, color))
    lines.append("    " + paint(f"{counts[OK]} OK   {counts[WARN]} WARN   {counts[FAIL]} FAIL",
                                "2", enabled=color))
    lines.append("")

    problems = total - ok_count
    if problems:
        summary = f"{problems} item(s) need attention."
        lines.append("  " + paint(summary, "33" if problems else "32", enabled=color))
    else:
        lines.append("  " + paint("Everything looks good.", "32", enabled=color))
    return "\n".join(lines)


def stacked_bar(counts, width=30, color=False):
    """One bar split into coloured parts, one part per status."""
    total = sum(counts.values())
    if not total:
        return paint("░" * width, "2", enabled=color)
    parts = []
    for status in (OK, WARN, FAIL):
        size = round(counts[status] * width / total)
        if size:
            parts.append(paint("█" * size, COLORS[status], enabled=color))
    return "".join(parts)


def bar(percent, width=30, code="32", color=False):
    """A horizontal bar: filled blocks for the percent, light blocks for the rest."""
    percent = max(0, min(100, percent))
    filled = round(percent * width / 100)
    return (paint("█" * filled, code, enabled=color)
            + paint("░" * (width - filled), "2", enabled=color))


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
    parser.add_argument(
        "--plain", action="store_true",
        help="no colors, for logs and copy and paste",
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
        # Colors only when printing to a real terminal, and never when NO_COLOR is set.
        color = sys.stdout.isatty() and not args.plain and "NO_COLOR" not in os.environ
        print(render_report(results, color=color))
    if args.fix:
        run_fixes(results)
    return exit_code(results)


if __name__ == "__main__":
    raise SystemExit(main())
