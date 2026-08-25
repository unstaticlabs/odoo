#!/usr/bin/env python3
"""Fail before reconstruction when a shared Docker VM cannot be used safely."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass

GIB = 1024 ** 3
DEFAULT_SHARED_VM_MINIMUM = 12 * GIB


class PreflightError(RuntimeError):
    pass


@dataclass(frozen=True)
class CapacityDecision:
    accepted: bool
    reason: str


def evaluate_capacity(
    *,
    total_memory: int,
    current_project: str,
    running_projects: set[str],
    production: bool,
    allow_concurrent: bool,
    shared_vm_minimum: int = DEFAULT_SHARED_VM_MINIMUM,
) -> CapacityDecision:
    foreign = sorted(running_projects - {current_project})
    if production and foreign:
        return CapacityDecision(
            False,
            "production reconstruction requires a dedicated Docker runtime; "
            f"foreign Compose projects are running: {', '.join(foreign)}",
        )
    if foreign and total_memory < shared_vm_minimum and not allow_concurrent:
        return CapacityDecision(
            False,
            f"Docker has {total_memory / GIB:.1f} GiB and foreign Compose projects "
            f"are running ({', '.join(foreign)}); this combination previously "
            "OOM-killed the atomic Accounting import",
        )
    return CapacityDecision(True, "Docker capacity policy passed")


def docker_output(arguments: list[str]) -> str:
    try:
        result = subprocess.run(
            ["docker", *arguments],
            text=True,
            capture_output=True,
            check=False,
            timeout=15,
        )
    except subprocess.TimeoutExpired as error:
        raise PreflightError(
            "Docker did not answer within 15 seconds; no migration stage was started",
        ) from error
    if result.returncode:
        raise PreflightError(
            result.stderr.strip() or "Docker capacity inspection failed",
        )
    return result.stdout


def inspect_capacity() -> tuple[int, set[str]]:
    memory_text = docker_output(["info", "--format", "{{json .MemTotal}}"]).strip()
    try:
        total_memory = int(json.loads(memory_text))
    except (TypeError, ValueError, json.JSONDecodeError) as error:
        raise PreflightError(f"invalid Docker memory response: {memory_text!r}") from error
    projects = {
        line.strip()
        for line in docker_output([
            "ps",
            "--format",
            '{{.Label "com.docker.compose.project"}}',
        ]).splitlines()
        if line.strip()
    }
    return total_memory, projects


def main() -> int:
    current_project = os.environ.get("COMPOSE_PROJECT_NAME", "").strip()
    if not current_project:
        print("COMPOSE_PROJECT_NAME is required", file=sys.stderr)
        return 2
    purpose = os.environ.get("USL_MIGRATION_PURPOSE", "development")
    allow_concurrent = os.environ.get("USL_MIGRATION_ALLOW_CONCURRENT_DOCKER") == "1"
    if purpose == "production" and allow_concurrent:
        print(
            "USL_MIGRATION_ALLOW_CONCURRENT_DOCKER is forbidden in production",
            file=sys.stderr,
        )
        return 2
    try:
        total_memory, projects = inspect_capacity()
        decision = evaluate_capacity(
            total_memory=total_memory,
            current_project=current_project,
            running_projects=projects,
            production=purpose == "production",
            allow_concurrent=allow_concurrent,
        )
    except PreflightError as error:
        print(f"Migration Docker resource preflight failed: {error}", file=sys.stderr)
        return 2
    print(
        f"Docker memory: {total_memory / GIB:.1f} GiB; "
        f"running Compose projects: {', '.join(sorted(projects)) or 'none'}",
    )
    if not decision.accepted:
        print(f"Migration Docker resource preflight blocked: {decision.reason}", file=sys.stderr)
        print(
            "Do not stop foreign projects automatically. Have their owners quiesce "
            "them or allocate more Docker memory, then rerun from the clean reset stage.",
            file=sys.stderr,
        )
        return 1
    print(f"PASS: {decision.reason}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
