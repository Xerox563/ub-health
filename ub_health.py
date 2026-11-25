#!/usr/bin/env python3
"""ub-health: a plain English health check for Ubuntu."""

import argparse
import datetime
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
    results = [
        Result("Updates", WARN, "14 packages can be upgraded",
               fix="sudo apt update && sudo apt upgrade"),
        Result("Reboot", OK, "No restart needed"),
    ]
    print(render_report(results))


if __name__ == "__main__":
    main()
