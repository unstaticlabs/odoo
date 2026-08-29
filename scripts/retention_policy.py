#!/usr/bin/env python3
"""Plan 14-daily/8-weekly/12-monthly retention without weakening append-only storage."""

# ruff: noqa: EM101, T201, TRY301 - fail-closed operator CLI uses literal errors.

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import production_cohort  # noqa: E402
from continuous_operations_contracts import canonical_sha256  # noqa: E402


class RetentionError(ValueError):
    """Retention input cannot prove safe pruning."""


def _timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise RetentionError("timestamps must include a timezone")
    return parsed.astimezone(UTC)


def plan(entries: list[dict], now: datetime) -> dict:
    normalized = []
    ids: set[str] = set()
    for index, entry in enumerate(entries):
        if set(entry) != {"cohort", "append_only_until", "restore_verified"}:
            raise RetentionError(f"entries[{index}] has unexpected keys")
        cohort = production_cohort.validate(entry["cohort"])
        if cohort["cohort_id"] in ids:
            raise RetentionError("cohort IDs must be unique")
        ids.add(cohort["cohort_id"])
        if entry["restore_verified"] is not True:
            raise RetentionError(
                f"{cohort['cohort_id']} has no independent verified restore; pruning is forbidden",
            )
        normalized.append(
            (
                cohort,
                _timestamp(cohort["created_at"]),
                _timestamp(entry["append_only_until"]),
            ),
        )
    normalized.sort(key=lambda item: item[1], reverse=True)

    keep: set[str] = set()
    seen_days: set[str] = set()
    seen_weeks: set[str] = set()
    seen_months: set[str] = set()
    for cohort, created, _ in normalized:
        day = created.date().isoformat()
        iso = created.isocalendar()
        week = f"{iso.year}-W{iso.week:02d}"
        month = created.strftime("%Y-%m")
        if len(seen_days) < 14 and day not in seen_days:
            keep.add(cohort["cohort_id"])
            seen_days.add(day)
        if len(seen_weeks) < 8 and week not in seen_weeks:
            keep.add(cohort["cohort_id"])
            seen_weeks.add(week)
        if len(seen_months) < 12 and month not in seen_months:
            keep.add(cohort["cohort_id"])
            seen_months.add(month)

    delete = []
    blocked = []
    for cohort, _, append_only_until in normalized:
        cohort_id = cohort["cohort_id"]
        if cohort_id in keep:
            continue
        if now <= append_only_until:
            blocked.append(
                {"cohort_id": cohort_id, "reason": "append_only_window_active"},
            )
        else:
            delete.append(cohort_id)
    result = {
        "schema": "usl-retention-plan/v1",
        "policy": {"daily": 14, "weekly": 8, "monthly": 12},
        "keep": sorted(keep),
        "delete": sorted(delete),
        "blocked": sorted(blocked, key=lambda item: item["cohort_id"]),
    }
    return {**result, "plan_sha256": canonical_sha256(result)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--inventory", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--now")
    args = parser.parse_args()
    try:
        inventory = json.loads(Path(args.inventory).read_text(encoding="utf-8"))
        if not isinstance(inventory, list):
            raise RetentionError("inventory must be a list")
        now = _timestamp(args.now) if args.now else datetime.now(UTC)
        value = plan(inventory, now)
    except (OSError, json.JSONDecodeError, RetentionError, ValueError) as error:
        print(f"retention policy: {error}", file=sys.stderr)
        return 2
    Path(args.output).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
