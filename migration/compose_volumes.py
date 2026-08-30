#!/usr/bin/env python3
"""Resolve a service mount to its exact Docker volume from rendered Compose JSON."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


class ComposeVolumeError(ValueError):
    """The rendered Compose topology does not contain one unambiguous volume."""


def resolve(config: dict, service_name: str, target: str) -> str:
    services = config.get("services") or {}
    service = services.get(service_name)
    if not isinstance(service, dict):
        raise ComposeVolumeError(f"Compose service is missing: {service_name}")
    matches = [
        mount
        for mount in service.get("volumes") or []
        if isinstance(mount, dict)
        and mount.get("type") == "volume"
        and mount.get("target") == target
    ]
    if len(matches) != 1:
        raise ComposeVolumeError(
            f"expected one volume for {service_name}:{target}, found {len(matches)}"
        )
    source = matches[0].get("source")
    volumes = config.get("volumes") or {}
    definition = volumes.get(source)
    if not isinstance(source, str) or not isinstance(definition, dict):
        raise ComposeVolumeError(f"volume definition is missing for {service_name}:{target}")
    name = definition.get("name")
    if not isinstance(name, str) or not name:
        raise ComposeVolumeError(f"volume name is unresolved for {service_name}:{target}")
    return name


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path)
    parser.add_argument("service")
    parser.add_argument("target")
    args = parser.parse_args()
    try:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        print(resolve(config, args.service, args.target))
    except (OSError, json.JSONDecodeError, ComposeVolumeError) as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
