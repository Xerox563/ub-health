#!/usr/bin/env python3
"""ub-health: a plain English health check for Ubuntu."""

import argparse
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
    example = Result("Example", OK, "Nothing to check yet.")
    print(f"[{example.status}] {example.name}: {example.message}")


if __name__ == "__main__":
    main()
