#!/usr/bin/env python3
"""Resolve non-secret image identities from a rendered Compose config."""

# ruff: noqa: EM101, T201 - operator CLI reports concise literal failures.

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

REQUIRED_SERVICES = (
    "db",
    "odoo",
    "paperless-db",
    "paperless-webserver",
    "paperless-broker",
    "paperless-gotenberg",
    "paperless-tika",
)

OPTIONAL_SERVICES = ("paperless-ollama",)


class ImageIdentityError(ValueError):
    """Raised when a configured image cannot be resolved locally."""


def inspect(reference: str) -> dict:
    completed = subprocess.run(
        ["docker", "image", "inspect", reference],
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        raise ImageIdentityError(f"image is unavailable: {reference}")
    payload = json.loads(completed.stdout)
    if not isinstance(payload, list) or len(payload) != 1:
        raise ImageIdentityError(f"image identity is ambiguous: {reference}")
    image = payload[0]
    return {
        "reference": reference,
        "id": image.get("Id"),
        "repo_digests": sorted(image.get("RepoDigests") or []),
        "architecture": image.get("Architecture"),
        "os": image.get("Os"),
        "labels": image.get("Config", {}).get("Labels") or {},
    }


def build(config: dict, *, target_platform: str) -> dict:
    services = config.get("services") or {}
    missing = [name for name in REQUIRED_SERVICES if name not in services]
    if missing:
        raise ImageIdentityError("Compose services are missing: " + ", ".join(missing))
    images = {}
    for name in (*REQUIRED_SERVICES, *OPTIONAL_SERVICES):
        if name not in services:
            continue
        reference = services[name].get("image")
        if not reference:
            raise ImageIdentityError(f"Compose service has no image: {name}")
        images[name] = inspect(reference)
    target_arch = target_platform.rsplit("/", 1)[-1]
    target_ready = all(
        image["architecture"] == target_arch and image["repo_digests"]
        for image in images.values()
    )
    return {
        "schema": "usl-documents-image-identity-v1",
        "target_platform": target_platform,
        "target_platform_status": "passed" if target_ready else "partial",
        "images": images,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--compose-config", type=Path, required=True)
    parser.add_argument("--target-platform", default="linux/amd64")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        config = json.loads(args.compose_config.read_text(encoding="utf-8"))
        result = build(config, target_platform=args.target_platform)
    except (OSError, json.JSONDecodeError, ImageIdentityError) as error:
        raise SystemExit(f"Documents image identity rejected: {error}") from error
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    args.output.chmod(0o600)
    print(json.dumps({"status": result["target_platform_status"]}, sort_keys=True))


if __name__ == "__main__":
    main()
