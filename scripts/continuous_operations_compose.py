#!/usr/bin/env python3
"""Hash or compare the canonical continuous-operations Compose source."""

# ruff: noqa: T201 - operator CLI reports canonical drift evidence.

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CANONICAL = ROOT / "deploy" / "continuous-operations" / "compose.yaml"


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("hash")
    verify = sub.add_parser("verify")
    verify.add_argument("--vendored", required=True)
    args = parser.parse_args()
    try:
        canonical = digest(CANONICAL)
        if args.command == "hash":
            print(canonical)
            return 0
        vendored = digest(Path(args.vendored))
    except OSError as error:
        print(f"continuous operations compose: {error}", file=sys.stderr)
        return 2
    if vendored != canonical:
        print(
            f"continuous operations compose: drift detected "
            f"(canonical={canonical}, vendored={vendored})",
            file=sys.stderr,
        )
        return 2
    print(f"continuous operations compose: {canonical}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
