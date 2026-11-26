#!/usr/bin/env python3
"""ub-health: a plain English health check for Ubuntu."""

import argparse
import configparser
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


def check_firewall(ufw_text=None, service_state=None):
    """Report whether the ufw firewall is on. Falls back to the service state without sudo."""
    if ufw_text is None:
        try:
            proc = subprocess.run(["ufw", "status"], capture_output=True, text=True)
        except FileNotFoundError:
            return Result("Firewall", WARN, "The firewall (ufw) is not installed",
                          fix="sudo apt install ufw")
        ufw_text = proc.stdout + proc.stderr
    if "Status: active" in ufw_text:
        return Result("Firewall", OK, "Firewall is on")
    if "Status: inactive" in ufw_text:
        return Result("Firewall", WARN, "Firewall is off", fix="sudo ufw enable")
    if "need to be root" in ufw_text.lower():
        if service_state is None:
            service_state = subprocess.run(
                ["systemctl", "is-active", "ufw"], capture_output=True, text=True,
            ).stdout.strip()
        if service_state == "active":
            return Result("Firewall", OK, "Firewall service is running (sudo shows the rules)")
        return Result("Firewall", WARN, "Firewall service is not running",
                      fix="sudo ufw enable")
    return Result("Firewall", WARN, "Could not read firewall status", fix="sudo ub-health")


JOURNAL_WARN_COUNT = 50


def parse_journal_errors(text):
    """Return (count, noisiest program) from `journalctl -p err` lines."""
    programs = {}
    count = 0
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        count += 1
        program = parts[4].split("[", 1)[0].rstrip(":")
        programs[program] = programs.get(program, 0) + 1
    top = max(programs, key=programs.get) if programs else ""
    return count, top


def check_journal(text=None):
    """Count error messages from the last 24 hours in the system log."""
    if text is None:
        proc = subprocess.run(
            ["journalctl", "-p", "err", "--since", "24 hours ago", "--no-pager", "-q"],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            return Result("System log", WARN, "Could not read the system log (run with sudo)",
                          fix="sudo ub-health")
        text = proc.stdout
    count, top = parse_journal_errors(text)
    if count == 0:
        return Result("System log", OK, "No error messages in the last 24 hours")
    message = f"{count} error messages in the last 24 hours"
    if top:
        message += f" (most from {top})"
    if count >= JOURNAL_WARN_COUNT:
        return Result("System log", WARN, message)
    return Result("System log", OK, message)


AUTO_UPGRADES_FILE = "/etc/apt/apt.conf.d/20auto-upgrades"


def check_auto_updates(config_text=None):
    """Report whether security updates install themselves automatically."""
    if config_text is None:
        try:
            with open(AUTO_UPGRADES_FILE) as handle:
                config_text = handle.read()
        except OSError:
            config_text = ""
    enabled = any(
        line.split() and line.split()[0] == "APT::Periodic::Unattended-Upgrade"
        and line.split()[-1].strip('";') == "1"
        for line in config_text.splitlines()
    )
    if enabled:
        return Result("Auto updates", OK, "Security updates install automatically")
    return Result(
        "Auto updates", WARN, "Security updates do not install automatically",
        fix="sudo apt install unattended-upgrades && sudo dpkg-reconfigure -plow unattended-upgrades",
    )


MEM_WARN_FREE_PERCENT = 10
MEM_FAIL_FREE_PERCENT = 5


def parse_meminfo(text):
    """Turn /proc/meminfo text into {"MemTotal": kB, ...}."""
    values = {}
    for line in text.splitlines():
        if ":" not in line:
            continue
        key, rest = line.split(":", 1)
        fields = rest.split()
        if fields and fields[0].isdigit():
            values[key.strip()] = int(fields[0])
    return values


def check_memory(meminfo_text=None):
    """Report how much memory and swap are in use."""
    if meminfo_text is None:
        try:
            with open("/proc/meminfo") as handle:
                meminfo_text = handle.read()
        except OSError:
            return Result("Memory", WARN, "Could not read memory information")
    info = parse_meminfo(meminfo_text)
    total = info.get("MemTotal", 0)
    available = info.get("MemAvailable")
    if not total or available is None:
        return Result("Memory", WARN, "Could not read memory information")
    free_percent = available * 100 // total
    used_gb = (total - available) / 1_000_000
    total_gb = total / 1_000_000
    details = [("Memory used", 100 - free_percent,
                f"{used_gb:.1f} of {total_gb:.1f} GB")]
    swap_total = info.get("SwapTotal", 0)
    if swap_total:
        swap_free = info.get("SwapFree", 0)
        swap_used = swap_total - swap_free
        details.append(("Swap used", swap_used * 100 // swap_total,
                        f"{swap_used / 1_000_000:.1f} of {swap_total / 1_000_000:.1f} GB"))
    message = f"{free_percent}% of memory is free"
    if free_percent < MEM_FAIL_FREE_PERCENT:
        status = FAIL
    elif free_percent < MEM_WARN_FREE_PERCENT:
        status = WARN
    else:
        status = OK
    return Result("Memory", status, message, details=details)


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


SPARK_CHARS = "▁▂▃▄▅▆▇█"


def sparkline(values):
    """Tiny chart: one character per value, from low (▁) to high (█). Values are 0 to 100."""
    chars = []
    for value in values:
        value = max(0, min(100, value))
        chars.append(SPARK_CHARS[round(value / 100 * (len(SPARK_CHARS) - 1))])
    return "".join(chars)


def render_report(results, today=None, color=False, history=None):
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
    if history and len(history) >= 2:
        trend = sparkline([entry.get("health", 0) for entry in history])
        lines.append(f"  Trend   {trend}  last {len(history)} runs")
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
            if len(label) > 16:
                label = label[:15] + "…"
            lines.append(f"    {label:<16} {bar(percent, 24, level_code(percent), color)}"
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


def render_markdown(results, today=None):
    """A Markdown report: a table of checks, then the fixes for anything that needs attention."""
    today = today or datetime.date.today()
    lines = [f"# Ubuntu Health Check, {today:%d %b %Y}", "",
             "| Check | Status | Details |", "| --- | --- | --- |"]
    for result in results:
        lines.append(f"| {result.name} | {result.status} | {result.message} |")
    fixes = [r for r in results if r.fix and r.status != OK]
    if fixes:
        lines += ["", "## Suggested fixes", ""]
        for result in fixes:
            lines.append(f"- **{result.name}**: `{result.fix}`")
    lines.append("")
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


CONFIG_FILE = os.path.expanduser("~/.config/ub-health/config.ini")


def _int_setting(parser, section, key, default):
    """Read a whole number from the config, or keep the default if it is missing or wrong."""
    try:
        return parser.getint(section, key)
    except (configparser.Error, ValueError):
        return default


def apply_config(config_path=CONFIG_FILE):
    """Load warning and failure limits from the config file. Missing file means defaults."""
    global DISK_WARN_PERCENT, DISK_FAIL_PERCENT, MEM_WARN_FREE_PERCENT, MEM_FAIL_FREE_PERCENT
    global CERT_WARN_DAYS, CERT_FAIL_DAYS, JOURNAL_WARN_COUNT
    parser = configparser.ConfigParser()
    try:
        parser.read(config_path)
    except configparser.Error:
        return
    DISK_WARN_PERCENT = _int_setting(parser, "disk", "warn_percent", DISK_WARN_PERCENT)
    DISK_FAIL_PERCENT = _int_setting(parser, "disk", "fail_percent", DISK_FAIL_PERCENT)
    MEM_WARN_FREE_PERCENT = _int_setting(parser, "memory", "warn_free_percent",
                                         MEM_WARN_FREE_PERCENT)
    MEM_FAIL_FREE_PERCENT = _int_setting(parser, "memory", "fail_free_percent",
                                         MEM_FAIL_FREE_PERCENT)
    CERT_WARN_DAYS = _int_setting(parser, "certificates", "warn_days", CERT_WARN_DAYS)
    CERT_FAIL_DAYS = _int_setting(parser, "certificates", "fail_days", CERT_FAIL_DAYS)
    JOURNAL_WARN_COUNT = _int_setting(parser, "journal", "warn_count", JOURNAL_WARN_COUNT)


HISTORY_PATH = os.path.expanduser("~/.local/state/ub-health/history.jsonl")


def history_entry(results, now=None):
    """One short record of a run: when it happened and how many checks were OK."""
    now = now or datetime.datetime.now()
    total = len(results)
    ok_count = sum(1 for r in results if r.status == OK)
    return {
        "time": now.isoformat(timespec="seconds"),
        "health": ok_count * 100 // total if total else 100,
        "ok": ok_count,
        "warn": sum(1 for r in results if r.status == WARN),
        "fail": sum(1 for r in results if r.status == FAIL),
    }


def save_history(results, path=HISTORY_PATH, now=None):
    """Append this run to the history file. Creates the folder if needed."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "a") as handle:
        handle.write(json.dumps(history_entry(results, now)) + "\n")


def load_history(path=HISTORY_PATH, limit=20):
    """Return the last `limit` runs. Damaged lines are skipped, never fatal."""
    try:
        with open(path) as handle:
            lines = handle.read().splitlines()
    except OSError:
        return []
    entries = []
    for line in lines:
        try:
            entries.append(json.loads(line))
        except ValueError:
            continue
    return entries[-limit:]


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
    parser.add_argument(
        "--no-history", action="store_true",
        help="do not save this run to the history file",
    )
    parser.add_argument(
        "--report", metavar="FILE",
        help="save the results as a Markdown report to FILE",
    )
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    apply_config()
    if not is_ubuntu():
        print("ub-health only works on Ubuntu. This system does not look like Ubuntu.")
        return 3
    results = [check_updates(refresh=args.refresh), check_reboot(), check_services(),
               check_disk(), check_memory(), check_firewall(), check_auto_updates(),
               check_journal(), check_certificates()]
    if args.json:
        print(json.dumps([asdict(r) for r in results], indent=2))
    if args.report:
        with open(args.report, "w") as handle:
            handle.write(render_markdown(results))
        print(f"Saved report to {args.report}")
    if args.fix:
        run_fixes(results)
    if not args.no_history:
        try:
            save_history(results)
        except OSError:
            pass  # History is a nice extra. Never let it break the check.
    if not args.json:
        history = load_history()
        color = sys.stdout.isatty() and not args.plain and "NO_COLOR" not in os.environ
        print(render_report(results, color=color, history=history))
    return exit_code(results)


if __name__ == "__main__":
    raise SystemExit(main())
