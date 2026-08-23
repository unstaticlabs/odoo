# ruff: noqa: T201
"""Seal and verify the reusable Paperless ingestion stage.

The checkpoint is migration evidence, not product state.  It proves that an
existing Paperless archive still matches a compatible runtime contract and
the archive roots from a previously successful restore.  Source and code
changes are recorded and reconciled by the idempotent importer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path

# Bump this contract when a migration change cannot reconcile an existing
# archive in place.  A bump deliberately requires one fresh ingestion.
SCHEMA = "usl-paperless-ingestion-checkpoint-v2"
REUSE_CONTRACT = "paperless-archive-exact-input-v1"
TRANSFORMATION_INPUTS = (
    "migration/documents_archive/classification.py",
    "migration/documents_archive/scripts/paperless_migration_access.py",
    "migration/documents_archive/scripts/source_documents_restore.py",
    "custom-addons/usl_documents/__manifest__.py",
    "custom-addons/usl_documents/data",
    "custom-addons/usl_documents/migrations",
    "custom-addons/usl_documents/models",
    "custom-addons/usl_documents/security",
    "custom-addons/usl_documents_accounting/__manifest__.py",
    "custom-addons/usl_documents_accounting/models",
)
INCLUDED_SUFFIXES = {".csv", ".py", ".xml"}


class CheckpointError(ValueError):
    """Raised when an archive cannot be reused safely."""


def _iter_input_files(root: Path, inputs: tuple[str, ...]) -> list[Path]:
    files: list[Path] = []
    for relative_name in inputs:
        path = root / relative_name
        if path.is_file():
            files.append(path)
        elif path.is_dir():
            files.extend(
                child
                for child in path.rglob("*")
                if child.is_file()
                and child.suffix in INCLUDED_SUFFIXES
                and "__pycache__" not in child.parts
            )
        else:
            raise CheckpointError(f"checkpoint input is missing: {relative_name}")
    return sorted(set(files), key=lambda path: path.relative_to(root).as_posix())


def transformation_digest(
    root: Path,
    inputs: tuple[str, ...] = TRANSFORMATION_INPUTS,
) -> str:
    """Hash migration behavior by path and content, independent of Git state."""
    digest = hashlib.sha256()
    for path in _iter_input_files(root, inputs):
        relative_name = path.relative_to(root).as_posix().encode()
        digest.update(len(relative_name).to_bytes(4, "big"))
        digest.update(relative_name)
        content = path.read_bytes()
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def reuse_identity(runtime: dict[str, str]) -> dict:
    return {
        "reuse_contract": REUSE_CONTRACT,
        "runtime": dict(sorted(runtime.items())),
    }


def current_provenance(
    root: Path,
    source_sha256: str,
    inputs: tuple[str, ...] = TRANSFORMATION_INPUTS,
) -> dict:
    if len(source_sha256) != 64 or any(
        character not in "0123456789abcdef" for character in source_sha256
    ):
        message = "source SHA-256 must be 64 lowercase hex characters"
        raise CheckpointError(message)
    return {
        "source_dump_sha256": source_sha256,
        "transformation_sha256": transformation_digest(root, inputs),
    }


def checkpoint_payload(identity: dict, provenance: dict, archive: dict) -> dict:
    return {
        "schema": SCHEMA,
        "identity": identity,
        "provenance": provenance,
        "archive": archive,
    }


def verify_payload(
    payload: dict,
    identity: dict,
    provenance: dict,
    archive: dict,
) -> list[str]:
    if payload.get("schema") != SCHEMA:
        message = "checkpoint schema is missing or unsupported"
        raise CheckpointError(message)
    mismatches = []
    for key, value in identity.items():
        if payload.get("identity", {}).get(key) != value:
            mismatches.append(key)
    for key, value in archive.items():
        if payload.get("archive", {}).get(key) != value:
            mismatches.append(f"archive.{key}")
    if mismatches:
        raise CheckpointError(
            "reusable Paperless stage does not match: " + ", ".join(mismatches),
        )
    return [
        key
        for key, value in provenance.items()
        if payload.get("provenance", {}).get(key) != value
    ]


def write_checkpoint(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def read_checkpoint(path: Path) -> dict:
    if not path.is_file():
        raise CheckpointError(f"checkpoint is missing: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CheckpointError(f"checkpoint is unreadable: {path}") from error


def _key_values(values: list[str]) -> dict[str, str]:
    result = {}
    for value in values:
        key, separator, item = value.partition("=")
        if not separator or not key or key in result:
            raise CheckpointError(f"invalid or duplicate runtime value: {value}")
        result[key] = item
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=("seal", "verify"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--source-sha256", required=True)
    parser.add_argument("--runtime", action="append", default=[])
    parser.add_argument("--archive-sha256", required=True)
    parser.add_argument("--document-count", type=int, required=True)
    parser.add_argument("--trash-count", type=int, required=True)
    return parser


def main() -> None:
    args = _parser().parse_args()
    try:
        runtime = _key_values(args.runtime)
        identity = reuse_identity(runtime)
        provenance = current_provenance(
            args.root.resolve(),
            args.source_sha256,
        )
        archive = {
            "document_count": args.document_count,
            "roots_sha256": args.archive_sha256,
            "trash_count": args.trash_count,
        }
        if args.operation == "seal":
            write_checkpoint(
                args.checkpoint,
                checkpoint_payload(identity, provenance, archive),
            )
            print(f"Paperless ingestion checkpoint sealed: {args.checkpoint}")
        else:
            changes = verify_payload(
                read_checkpoint(args.checkpoint),
                identity,
                provenance,
                archive,
            )
            print(f"Paperless ingestion checkpoint verified: {args.checkpoint}")
            if changes:
                print(
                    "Documents inputs changed and will be reconciled "
                    "incrementally: " + ", ".join(changes),
                )
    except CheckpointError as error:
        raise SystemExit(
            f"Paperless ingestion checkpoint rejected: {error}. "
            "Run the normal fresh reconstruction to rebuild it.",
        ) from error


if __name__ == "__main__":
    main()
