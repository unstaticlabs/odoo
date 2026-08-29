#!/usr/bin/env python3
"""Maintain fail-closed private protection for the evolving transition stack."""

from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path


SCHEMA = "usl-transition-live-state-v1"
PROTECTED_STATUSES = {"transition-live", "frozen-read-only"}
PROJECT = re.compile(r"usl-odoo-transition-[a-z0-9][a-z0-9-]*\Z")


class TransitionStateError(RuntimeError):
    """Raised when transition protection cannot be trusted."""


def state_root(root: Path) -> Path:
    configured = os.environ.get("USL_TRANSITION_STATE_ROOT")
    return Path(configured).expanduser().resolve() if configured else root / ".agent/transition-live"


def state_path(root: Path, project: str) -> Path:
    if not PROJECT.fullmatch(project):
        raise TransitionStateError(
            "transition protection requires a usl-odoo-transition-* Compose project",
        )
    return state_root(root) / f"{project}.json"


def read_state(path: Path, project: str) -> dict | None:
    if not path.exists():
        return None
    if path.is_symlink() or not path.is_file():
        raise TransitionStateError(f"unsafe transition state path: {path}")
    if stat.S_IMODE(path.parent.stat().st_mode) != 0o700:
        raise TransitionStateError(f"transition state directory is not mode 0700: {path.parent}")
    if stat.S_IMODE(path.stat().st_mode) != 0o600:
        raise TransitionStateError(f"transition state file is not mode 0600: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise TransitionStateError(f"unreadable transition state: {path}") from error
    if value.get("schema") != SCHEMA or value.get("compose_project") != project:
        raise TransitionStateError(f"invalid transition state identity: {path}")
    if value.get("status") not in PROTECTED_STATUSES:
        raise TransitionStateError(f"unsupported transition state status: {path}")
    return value


def atomic_write(path: Path, value: dict) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def mark(args: argparse.Namespace) -> dict:
    if args.confirm != args.project:
        raise TransitionStateError("mark requires --confirm with the exact Compose project")
    path = state_path(args.root, args.project)
    existing = read_state(path, args.project)
    if existing:
        if existing.get("database") != args.database:
            raise TransitionStateError("transition state already protects another database")
        return existing
    value = {
        "schema": SCHEMA,
        "status": "transition-live",
        "compose_project": args.project,
        "database": args.database,
        "created_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "source_commit": args.commit,
        "protection": {
            "qa_bootstrap": False,
            "qa_seed_publication": False,
            "reconstruction": False,
            "target_reset": False,
            "test_helpers": False,
        },
    }
    atomic_write(path, value)
    return value


def freeze(args: argparse.Namespace) -> dict:
    if args.confirm != args.project:
        raise TransitionStateError("freeze requires --confirm with the exact Compose project")
    path = state_path(args.root, args.project)
    value = read_state(path, args.project)
    if not value:
        raise TransitionStateError("transition project must be marked live before it can be frozen")
    value["status"] = "frozen-read-only"
    value["frozen_at"] = datetime.now(UTC).replace(microsecond=0).isoformat()
    atomic_write(path, value)
    return value


def guard(args: argparse.Namespace) -> dict:
    path = state_path(args.root, args.project)
    value = read_state(path, args.project)
    if value:
        raise TransitionStateError(
            f"{args.operation} is forbidden for {args.project}: "
            f"private transition state is {value['status']}",
        )
    return {"compose_project": args.project, "protected": False}


def status(args: argparse.Namespace) -> dict:
    value = read_state(state_path(args.root, args.project), args.project)
    return value or {"compose_project": args.project, "protected": False}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--root", type=Path, required=True)
    subparsers = result.add_subparsers(dest="command", required=True)
    for command in ("guard", "status", "mark", "freeze"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--project", required=True)
        if command == "guard":
            subparser.add_argument("--operation", required=True)
        if command == "mark":
            subparser.add_argument("--database", default="odoo_dev")
            subparser.add_argument("--commit", required=True)
            subparser.add_argument("--confirm", required=True)
        if command == "freeze":
            subparser.add_argument("--confirm", required=True)
    return result


def main() -> None:
    args = parser().parse_args()
    try:
        value = globals()[args.command](args)
    except TransitionStateError as error:
        raise SystemExit(f"Transition protection refused: {error}") from error
    print(json.dumps(value, sort_keys=True))


if __name__ == "__main__":
    main()
