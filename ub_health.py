#!/usr/bin/env python3
"""ub-health: a plain English health check for Ubuntu."""

import argparse
import datetime
import os
import subprocess
from dataclasses import dataclass, field

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


def check_updates(apt_output=None):
    """Report how many packages can be upgraded, flagging security updates."""
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


def build_parser():
    parser = argparse.ArgumentParser(
        prog="ub-health",
        description="Check your Ubuntu computer and explain what needs attention.",
    )
    parser.add_argument(
        "--version", action="version", version=f"ub-health {VERSION}"
    )
    return parser


def main(argv=None):
    build_parser().parse_args(argv)
    results = [check_updates(), check_reboot(), check_services()]
    print(render_report(results))
    return exit_code(results)


if __name__ == "__main__":
    raise SystemExit(main())
