#!/usr/bin/env python3
"""Resolve deterministic build identities for independently released components."""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import os
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "usl-component-inputs/v1"


class ComponentBuildError(RuntimeError):
    """A component definition or tracked build input is invalid."""


@dataclass(frozen=True)
class Component:
    name: str
    image: str
    dockerfile: str
    target: str | None
    patterns: tuple[str, ...]
    excludes: tuple[str, ...] = ()


COMPONENTS = {
    component.name: component
    for component in (
        Component(
            "distribution",
            "ghcr.io/unstaticlabs/usl-odoo",
            "Dockerfile",
            "distribution",
            (
                "Dockerfile.dockerignore",
                "Dockerfile",
                "MANIFEST.in",
                "requirements.txt",
                "setup.py",
                "odoo-bin",
                "debian/odoo.conf",
                "docker/constraints.txt",
                "docker/entrypoint.sh",
                "docker/odoo.conf.template",
                "setup/**",
                "odoo/**",
                "addons/**",
                "custom-addons/**",
                "docs/users/**",
                "oca-patches/**",
                "scripts/sync-oca-addons",
            ),
            ("custom-addons/usl_bootstrap/**",),
        ),
        Component(
            "backup-tool",
            "ghcr.io/unstaticlabs/usl-odoo-backup",
            "docker/backup.Dockerfile",
            None,
            (
                "docker/backup.Dockerfile",
                "docker/backup.Dockerfile.dockerignore",
                "compose.resources.production.json",
                "compose.resources.staging.json",
                "deploy/production.cron-policy.json",
                "operations/**",
                "operations/contracts/**",
                "scripts/cohort-runtime",
                "scripts/usl-stack",
            ),
        ),
        Component(
            "paperless",
            "ghcr.io/unstaticlabs/usl-paperless-ngx",
            "deploy/documents/paperless-ngx/Dockerfile",
            None,
            ("deploy/documents/paperless-ngx/**",),
        ),
        Component(
            "sign-dss",
            "ghcr.io/unstaticlabs/usl-sign-dss",
            "services/usl-sign-dss/Dockerfile",
            None,
            (
                "services/usl-sign-dss/**",
                "addons/web/static/fonts/sign/NotoSans-Reg.ttf",
            ),
        ),
    )
}


def _tracked_files(root: Path = ROOT) -> list[str]:
    process = subprocess.run(
        ("git", "ls-files", "-z"),
        cwd=root,
        check=False,
        capture_output=True,
    )
    if process.returncode:
        raise ComponentBuildError(process.stderr.decode(errors="replace").strip())
    return sorted(filter(None, process.stdout.decode().split("\0")))


def _matches(path: str, patterns: tuple[str, ...]) -> bool:
    return any(
        fnmatch.fnmatchcase(path, pattern)
        or (pattern.endswith("/**") and path.startswith(pattern[:-3] + "/"))
        for pattern in patterns
    )


def component_files(component: Component, root: Path = ROOT) -> list[str]:
    files = [
        path
        for path in _tracked_files(root)
        if _matches(path, component.patterns)
        and not _matches(path, component.excludes)
    ]
    if not files:
        raise ComponentBuildError(f"component {component.name!r} has no tracked inputs")
    if component.dockerfile not in files:
        raise ComponentBuildError(
            f"component {component.name!r} does not include its Dockerfile",
        )
    return files


def component_digest(component: Component, root: Path = ROOT) -> str:
    digest = hashlib.sha256()
    digest.update((SCHEMA + "\0" + component.name + "\0").encode())
    for relative in component_files(component, root):
        path = root / relative
        metadata = path.lstat()
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(oct(stat.S_IMODE(metadata.st_mode)).encode())
        digest.update(b"\0")
        if path.is_symlink():
            digest.update(b"symlink\0")
            digest.update(os.readlink(path).encode())
        else:
            digest.update(b"file\0")
            with path.open("rb") as stream:
                while chunk := stream.read(1024 * 1024):
                    digest.update(chunk)
        digest.update(b"\0")
    return digest.hexdigest()


def resolve(root: Path = ROOT) -> dict[str, object]:
    values = {}
    for name, component in sorted(COMPONENTS.items()):
        digest = component_digest(component, root)
        values[name] = {
            "input_sha256": digest,
            "image": component.image,
            "tag": f"content-{digest}",
            "dockerfile": component.dockerfile,
            "target": component.target or "",
        }
    return {"schema": SCHEMA, "components": values}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("component", nargs="?", choices=sorted(COMPONENTS))
    parser.add_argument("--json", action="store_true")
    arguments = parser.parse_args()
    try:
        payload = resolve()
    except ComponentBuildError as error:
        print(f"component build: {error}", file=sys.stderr)
        return 2
    if arguments.component:
        value = payload["components"][arguments.component]
        if arguments.json:
            print(json.dumps(value, sort_keys=True))
        else:
            print(value["input_sha256"])
    else:
        print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
