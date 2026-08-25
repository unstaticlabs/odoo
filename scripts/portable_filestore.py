#!/usr/bin/env python3
"""Build and verify a minimal, checksum-qualified Odoo filestore archive."""

# Operator helper with concise fail-closed errors and intentional JSON output.
# ruff: noqa: EM101, T201

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
import re
import stat
import tarfile
import tempfile
from pathlib import Path
from typing import NamedTuple

STORE_NAME = re.compile(r"^[0-9a-f]{2}/[0-9a-f]{40}$")
CHECKSUM = re.compile(r"^[0-9a-f]{40}$")


class FilestoreError(ValueError):
    """Raised when the database inventory and filestore are not identical."""


class StoredFile(NamedTuple):
    name: str
    checksum: str
    size: int


def sha1_file(path: Path) -> str:
    digest = hashlib.sha1(usedforsecurity=False)
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_inventory(path: Path) -> list[StoredFile]:
    if path.is_symlink() or not path.is_file():
        raise FilestoreError("attachment inventory must be a regular file")
    by_name: dict[str, StoredFile] = {}
    with path.open(newline="", encoding="utf-8") as stream:
        for number, row in enumerate(csv.reader(stream, delimiter="\t"), 1):
            if len(row) != 3:
                raise FilestoreError(
                    f"invalid attachment inventory row {number}",
                )
            name, checksum, raw_size = row
            if not STORE_NAME.fullmatch(name) or not CHECKSUM.fullmatch(checksum):
                raise FilestoreError(
                    f"unsafe attachment identity at inventory row {number}",
                )
            try:
                size = int(raw_size)
            except ValueError as error:
                raise FilestoreError(
                    f"invalid attachment size at inventory row {number}",
                ) from error
            if size < 0:
                raise FilestoreError(
                    f"negative attachment size at inventory row {number}",
                )
            item = StoredFile(name, checksum, size)
            previous = by_name.setdefault(name, item)
            if previous != item:
                raise FilestoreError(
                    f"conflicting database identities for stored file {name}",
                )
    if not by_name:
        raise FilestoreError("attachment inventory is empty")
    return [by_name[name] for name in sorted(by_name)]


def verify_source(root: Path, inventory: list[StoredFile]) -> dict:
    if root.is_symlink() or not root.is_dir():
        raise FilestoreError("filestore root must be a regular directory")
    expected = {item.name for item in inventory}
    observed = set()
    for directory, directories, files in os.walk(root, followlinks=False):
        parent = Path(directory)
        for name in directories:
            path = parent / name
            if path.is_symlink():
                raise FilestoreError(f"filestore directory is a symlink: {path}")
        for name in files:
            path = parent / name
            if path.is_symlink() or not path.is_file():
                raise FilestoreError(f"filestore entry is not a regular file: {path}")
            observed.add(path.relative_to(root).as_posix())
    missing = sorted(expected - observed)
    unexpected = sorted(observed - expected)
    if missing:
        raise FilestoreError(
            f"database-referenced filestore files are missing: {missing[:5]}",
        )
    if unexpected:
        raise FilestoreError(
            f"unreferenced files remain in portable filestore: {unexpected[:5]}",
        )

    total_size = 0
    for item in inventory:
        path = root / item.name
        mode = path.stat().st_mode
        if not stat.S_ISREG(mode) or path.stat().st_size != item.size:
            raise FilestoreError(f"stored attachment size differs: {item.name}")
        if sha1_file(path) != item.checksum:
            raise FilestoreError(
                f"stored attachment checksum differs: {item.name}",
            )
        total_size += item.size
    return {
        "distinct_store_file_count": len(inventory),
        "stored_bytes": total_size,
    }


def build_archive(root: Path, inventory: list[StoredFile], archive: Path) -> dict:
    # The source may contain harmless orphan files left by historical unlink
    # operations. Copy only files referenced by the sanitized database.
    for item in inventory:
        path = root / item.name
        if path.is_symlink() or not path.is_file():
            raise FilestoreError(f"stored attachment is missing or unsafe: {item.name}")
        if path.stat().st_size != item.size or sha1_file(path) != item.checksum:
            raise FilestoreError(f"stored attachment differs from Odoo: {item.name}")

    archive.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{archive.name}.",
        dir=archive.parent,
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with temporary.open("wb") as raw:
            with gzip.GzipFile(
                filename="",
                fileobj=raw,
                mode="wb",
                mtime=0,
            ) as compressed:
                with tarfile.open(fileobj=compressed, mode="w") as tar:
                    for item in inventory:
                        path = root / item.name
                        info = tarfile.TarInfo(item.name)
                        info.size = item.size
                        info.mode = 0o600
                        info.mtime = 0
                        info.uid = 1000
                        info.gid = 1000
                        info.uname = "odoo"
                        info.gname = "odoo"
                        with path.open("rb") as stream:
                            tar.addfile(info, stream)
        temporary.replace(archive)
        archive.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "archive_sha256": sha256_file(archive),
        "distinct_store_file_count": len(inventory),
        "stored_bytes": sum(item.size for item in inventory),
    }


def verify_archive(archive: Path, inventory: list[StoredFile]) -> dict:
    expected = {item.name: item for item in inventory}
    observed = set()
    with tarfile.open(archive, "r:gz") as stream:
        for member in stream.getmembers():
            if (
                not member.isfile()
                or not STORE_NAME.fullmatch(member.name)
                or member.name in observed
            ):
                raise FilestoreError(
                    f"portable filestore archive member is unsafe: {member.name}",
                )
            item = expected.get(member.name)
            if not item or member.size != item.size:
                raise FilestoreError(
                    f"portable filestore archive identity differs: {member.name}",
                )
            extracted = stream.extractfile(member)
            if extracted is None:
                raise FilestoreError(
                    f"cannot read portable filestore member: {member.name}",
                )
            digest = hashlib.sha1(usedforsecurity=False)
            for chunk in iter(lambda: extracted.read(1024 * 1024), b""):
                digest.update(chunk)
            if digest.hexdigest() != item.checksum:
                raise FilestoreError(
                    f"portable filestore archive checksum differs: {member.name}",
                )
            if (member.uid, member.gid, member.mode) != (1000, 1000, 0o600):
                raise FilestoreError(
                    f"portable filestore ownership/mode is unsafe: {member.name}",
                )
            observed.add(member.name)
    missing = sorted(set(expected) - observed)
    if missing:
        raise FilestoreError(
            f"portable filestore archive is missing files: {missing[:5]}",
        )
    return {
        "archive_sha256": sha256_file(archive),
        "distinct_store_file_count": len(observed),
        "stored_bytes": sum(item.size for item in inventory),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("build", "verify", "verify-archive"))
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--inventory", required=True, type=Path)
    parser.add_argument("--archive", type=Path)
    args = parser.parse_args()
    if args.command in {"build", "verify-archive"} and not args.archive:
        raise SystemExit(f"{args.command} requires --archive")
    try:
        inventory = read_inventory(args.inventory.resolve())
        if args.command == "build":
            result = build_archive(
                args.root.resolve(),
                inventory,
                args.archive.resolve(),
            )
        elif args.command == "verify":
            result = verify_source(args.root.resolve(), inventory)
        else:
            result = verify_archive(args.archive.resolve(), inventory)
    except (FilestoreError, OSError, tarfile.TarError) as error:
        raise SystemExit(f"Portable filestore rejected: {error}") from error
    print(json.dumps({"status": "passed", **result}, sort_keys=True))


if __name__ == "__main__":
    main()
