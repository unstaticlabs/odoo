#!/usr/bin/env python3
"""Refuse a deployed reconstruction stage without a qualified, current backup.

A stage that writes to a live database is only as safe as the point it can be
returned to. This reads the receipt `usl-stack backup create` leaves behind and
insists it qualified, that it captured the database about to be written, and
that it is recent enough to still describe it.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

RECEIPT_SCHEMA = "usl-backup-run/"
CAPTURE_SCHEMA = "usl-recovery-cohort/"
QUALIFIED = "qualified"


class BackupError(RuntimeError):
    """The receipt does not describe a usable rollback point."""


def _read(path: Path):
    try:
        return json.loads(path.read_bytes())
    except OSError as error:
        raise BackupError(f"{path} cannot be read: {error}") from error
    except json.JSONDecodeError as error:
        raise BackupError(f"{path} is not JSON: {error}") from error


def _timestamp(value):
    try:
        stamp = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as error:
        raise BackupError(f"{value!r} is not a timestamp: {error}") from error
    return stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc)


def verify(receipt_path, database, max_age_minutes, now=None):
    """Return the capture this receipt qualified, or raise."""
    now = now or datetime.now(timezone.utc)
    receipt_path = Path(receipt_path)
    receipt = _read(receipt_path)

    schema = str(receipt.get("schema", ""))
    if not schema.startswith(RECEIPT_SCHEMA):
        raise BackupError(f"{receipt_path} is {schema!r}, not a {RECEIPT_SCHEMA}* receipt.")
    status = receipt.get("status")
    if status != QUALIFIED:
        raise BackupError(f"{receipt_path} records status {status!r}, not {QUALIFIED!r}.")

    capture_path = receipt_path.parent / "capture.json"
    capture = _read(capture_path)
    capture_schema = str(capture.get("schema", ""))
    if not capture_schema.startswith(CAPTURE_SCHEMA):
        raise BackupError(
            f"{capture_path} is {capture_schema!r}, not a {CAPTURE_SCHEMA}* capture.",
        )

    captured = {
        entry.get("name")
        for entry in (capture.get("databases") or {}).values()
        if isinstance(entry, dict)
    }
    if database not in captured:
        raise BackupError(
            f"The backup captured {sorted(name for name in captured if name)!r}, "
            f"not {database!r}.",
        )

    created = _timestamp(capture.get("created_at"))
    age = now - created
    if age > timedelta(minutes=max_age_minutes):
        raise BackupError(
            f"The backup is {int(age.total_seconds() // 60)} minutes old, "
            f"older than the {max_age_minutes} minute limit.",
        )
    if age < timedelta(0):
        raise BackupError(f"The backup is dated in the future: {created.isoformat()}.")
    return capture


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--max-age-minutes", type=int, default=120)
    arguments = parser.parse_args(argv)
    try:
        capture = verify(
            arguments.receipt, arguments.database, arguments.max_age_minutes,
        )
    except BackupError as error:
        print(f"Refusing the deployed stage: {error}", file=sys.stderr)
        return 2
    print(
        f"Backup qualified for {arguments.database}: run {capture.get('run_id')} "
        f"captured {capture.get('created_at')}.",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
