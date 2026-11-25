#!/usr/bin/env python3
"""ub-health: a plain English health check for Ubuntu."""

import argparse
import datetime
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


def check_updates(apt_output=None):
    """Report how many packages can be upgraded."""
    if apt_output is None:
        apt_output = subprocess.run(
            ["apt", "list", "--upgradable"],
            capture_output=True, text=True,
        ).stdout
    count = len(parse_upgradable(apt_output))
    if count == 0:
        return Result("Updates", OK, "Everything is up to date")
    return Result(
        "Updates", WARN, f"{count} packages can be upgraded",
        fix="sudo apt update && sudo apt upgrade",
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
    results = [check_updates()]
    print(render_report(results))
    return exit_code(results)


if __name__ == "__main__":
    raise SystemExit(main())
