#!/usr/bin/env python3
# ruff: noqa: T201
"""Extract and seal private Documents restore evidence from an Odoo shell log."""

import argparse
import hashlib
import json
from pathlib import Path

PREFIX = "DOCUMENTS_SOURCE_RESTORE_RESULT="


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("log", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    results = [
        line[len(PREFIX):]
        for line in args.log.read_text(encoding="utf-8").splitlines()
        if line.startswith(PREFIX)
    ]
    if len(results) != 1:
        raise SystemExit(f"expected one restore result, found {len(results)}")
    result = json.loads(results[0])
    if result.get("schema") != "usl-documents-source-restore-result-v1":
        message = "unexpected Documents restore evidence schema"
        raise SystemExit(message)
    payload = (json.dumps(result, indent=2, sort_keys=True) + "\n").encode()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(args.output.suffix + ".tmp")
    temporary.write_bytes(payload)
    temporary.replace(args.output)
    digest = hashlib.sha256(payload).hexdigest()
    args.output.with_suffix(args.output.suffix + ".sha256").write_text(
        f"{digest}  {args.output.name}\n",
        encoding="utf-8",
    )
    print(f"Documents restore evidence: {args.output}")
    print(f"Documents restore evidence SHA-256: {digest}")


if __name__ == "__main__":
    main()
