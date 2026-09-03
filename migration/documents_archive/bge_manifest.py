#!/usr/bin/env python3
"""Build the non-secret qualified BGE-M3 release manifest."""

# ruff: noqa: EM101, T201 - operator CLI reports concise literal failures.

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ALIAS = "usl-bge-m3:documents-20260824-rc1"
EXPECTED_DIGEST = "7907646426070047a77226ac3e684fbbe8410524f7b4a74d02837e43f2146bab"


def build(
    show: str,
    digest: str,
    *,
    archive_sha256: str | None,
    archive_size: int | None,
    delivery_mode: str = "archive",
) -> dict:
    normalized = " ".join(show.split())
    required = {
        "architecture": r"architecture bert",
        "parameters": r"parameters 566\.70M",
        "context": r"context length 8192",
        "dimension": r"embedding length 1024",
        "quantization": r"quantization F16",
        "capability": r"Capabilities embedding",
        "license": r"License MIT License",
    }
    missing = [name for name, pattern in required.items() if not re.search(pattern, normalized)]
    status = (
        "passed"
        if not missing
        and digest == EXPECTED_DIGEST
        and (
            delivery_mode == "external-reference"
            or (
                delivery_mode == "archive"
                and archive_sha256 is not None
                and re.fullmatch(r"[0-9a-f]{64}", archive_sha256)
                and archive_size is not None
                and archive_size > 0
            )
        )
        else "partial"
    )
    return {
        "schema": "usl-bge-m3-release-model-v1",
        "status": status,
        "alias": ALIAS,
        "source_model": "bge-m3",
        "manifest_sha256": digest,
        "ollama_runtime": "0.30.11",
        "dimension": 1024,
        "context_length": 8192,
        "parameters": "566.70M",
        "quantization": "F16",
        "license": "MIT",
        "delivery_mode": delivery_mode,
        "model_archive_sha256": archive_sha256,
        "model_archive_size": archive_size,
        "missing_qualified_fields": missing,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--show", type=Path, required=True)
    parser.add_argument("--digest", required=True)
    parser.add_argument("--archive-sha256")
    parser.add_argument("--archive-size", type=int)
    parser.add_argument("--external-reference", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        result = build(
            args.show.read_text(encoding="utf-8"),
            args.digest,
            archive_sha256=args.archive_sha256,
            archive_size=args.archive_size,
            delivery_mode=(
                "external-reference" if args.external_reference else "archive"
            ),
        )
    except OSError as error:
        raise SystemExit(f"BGE-M3 manifest rejected: {error}") from error
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    args.output.chmod(0o600)
    print(json.dumps({"status": result["status"]}, sort_keys=True))


if __name__ == "__main__":
    main()
