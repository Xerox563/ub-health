#!/usr/bin/env python3
"""ub-health: a plain English health check for Ubuntu."""

import argparse

VERSION = "0.1.0"


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
    print("Ubuntu Health Check")
    print("Checks are not added yet.")


if __name__ == "__main__":
    main()
