#!/usr/bin/env python3
"""Refuse developer mutations against a recorded protected runtime."""

from __future__ import annotations

import argparse
import json
import stat
from pathlib import Path


PROTECTED = {"transition-live", "frozen-read-only"}


def guard(root: Path, project: str, operation: str) -> None:
    root = root.resolve()
    runtimes = root / "private/migration/runtimes"
    if not runtimes.is_dir():
        return
    for path in runtimes.glob("*/runtime.json"):
        if path.is_symlink() or stat.S_IMODE(path.stat().st_mode) != 0o600:
            raise SystemExit(f"Unsafe runtime state permissions: {path}")
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            raise SystemExit(f"Invalid runtime state: {path}") from error
        compose = value.get("compose") or {}
        if (
            value.get("schema") == "usl-migration-runtime-v1"
            and compose.get("project") == project
            and Path(compose.get("working_directory", "")).resolve() == root
            and value.get("status") in PROTECTED
        ):
            raise SystemExit(
                f"{operation} refused: {project} is recorded as {value['status']}."
            )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--operation", required=True)
    args = parser.parse_args()
    guard(args.root, args.project, args.operation)


if __name__ == "__main__":
    main()
