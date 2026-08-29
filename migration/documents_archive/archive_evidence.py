#!/usr/bin/env python3
"""Seal final Paperless archive identity as runtime-scoped evidence."""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from pathlib import Path


SCHEMA = "usl-paperless-archive-evidence-v1"
SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class EvidenceError(ValueError):
    pass


def assignments(values: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for value in values:
        key, separator, item = value.partition("=")
        if not separator or not key or key in result:
            raise EvidenceError(f"invalid or duplicate runtime value: {value}")
        result[key] = item
    return dict(sorted(result.items()))


def payload(
    source_sha256: str,
    runtime: dict[str, str],
    archive_sha256: str,
    document_count: int,
    trash_count: int,
) -> dict[str, object]:
    if not SHA256.fullmatch(source_sha256) or not SHA256.fullmatch(archive_sha256):
        raise EvidenceError("source and archive identities must be SHA-256 values")
    if document_count < 0 or trash_count < 0 or trash_count > document_count:
        raise EvidenceError("archive counts are invalid")
    return {
        "schema": SCHEMA,
        "source_dump_sha256": source_sha256,
        "runtime": dict(sorted(runtime.items())),
        "archive": {
            "document_count": document_count,
            "roots_sha256": archive_sha256,
            "trash_count": trash_count,
        },
    }


def write(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.parent.chmod(0o700)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--runtime", action="append", default=[])
    parser.add_argument("--archive-sha256", required=True)
    parser.add_argument("--document-count", type=int, required=True)
    parser.add_argument("--trash-count", type=int, required=True)
    args = parser.parse_args()
    try:
        value = payload(
            args.source_sha256,
            assignments(args.runtime),
            args.archive_sha256,
            args.document_count,
            args.trash_count,
        )
        write(args.output, value)
    except EvidenceError as error:
        raise SystemExit(f"Paperless archive evidence rejected: {error}") from error
    print(json.dumps(value, sort_keys=True))


if __name__ == "__main__":
    main()
