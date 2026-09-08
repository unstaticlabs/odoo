#!/usr/bin/env python3
"""Run the release's own upgrade-preservation gate against a local database.

The gate that refuses a release lives in ``operations.upgrade_preservation`` and
runs on a host, late, after a build and a deploy: on 2026-09-08 a change that it
refused took about seven hours and five failed releases to identify, because
nothing ran it any earlier.  This runs the identical code against the development
database, in the time an upgrade takes.

It is a real run of the gate, not an imitation, so it cannot drift from it.  What
it does *not* carry is production's data: it proves that upgrading these modules
rewrites rows the gate freezes, which is the failure class, not that a particular
production row survives.

The database must have been built BEFORE the changes under test, which is the
ordinary case: you build it once and then edit code.  Capturing and verifying
either side of an upgrade of a database already carrying those changes proves
nothing, and the run says so rather than reporting a false pass.
"""
from __future__ import annotations

import argparse
import json
import shlex
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from operations.upgrade_preservation import capture, verify  # noqa: E402


def _executor(psql: str):
    argv = shlex.split(psql)

    def execute(query: str) -> str:
        done = subprocess.run([*argv, query], capture_output=True, text=True)
        if done.returncode != 0:
            raise RuntimeError(
                "psql failed: " + (done.stderr.strip() or f"exit {done.returncode}"),
            )
        return done.stdout.strip()

    return execute


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("capture", "verify"))
    parser.add_argument("--psql", required=True, help="shell command taking one SQL argument")
    parser.add_argument("--baseline", required=True, type=Path)
    arguments = parser.parse_args()
    execute = _executor(arguments.psql)

    if arguments.phase == "capture":
        baseline = capture(execute)
        arguments.baseline.write_text(json.dumps(baseline, sort_keys=True) + "\n")
        counts = ", ".join(
            f"{table} {item['count']}"
            for table, item in sorted(baseline["fingerprints"].items())
        )
        print(f"Baseline captured: {counts}")
        return 0

    baseline = json.loads(arguments.baseline.read_text())
    proof = verify(baseline, execute)
    counts = ", ".join(
        f"{table} {item['count']}" for table, item in sorted(proof["fingerprints"].items())
    )
    print(f"Preserved: every pre-existing row is unchanged ({counts}).")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ValueError as error:
        print(
            f"Upgrade preservation would REFUSE this release: {error}\n"
            "A release that fails here rolls back, so the rollback restores the "
            "state that caused it and every retry fails identically. Either the "
            "change must stop rewriting those rows, or the rows must be excluded "
            "deliberately in operations/upgrade_preservation.py EXCLUDED_ROWS.",
            file=sys.stderr,
        )
        raise SystemExit(1) from error
