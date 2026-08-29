#!/usr/bin/env python3
"""Create a deterministic portable archive for one qualified Ollama model."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import json
import os
import re
import tarfile
import tempfile
from pathlib import Path, PurePosixPath


DIGEST = re.compile(r"sha256:([0-9a-f]{64})\Z")
MODEL_MANIFEST = PurePosixPath(
    "models/manifests/registry.ollama.ai/library/usl-bge-m3/"
    "documents-20260824-rc1",
)


class ModelArchiveError(ValueError):
    """Raised when a native model cannot be safely archived."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def referenced_files(models_root: Path) -> list[tuple[Path, PurePosixPath]]:
    manifest = models_root / MODEL_MANIFEST.relative_to("models")
    try:
        payload = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ModelArchiveError("qualified native Ollama manifest is unreadable") from error
    digests = [payload.get("config", {}).get("digest")]
    digests.extend(layer.get("digest") for layer in payload.get("layers", []))
    if not digests or any(not isinstance(value, str) for value in digests):
        raise ModelArchiveError("qualified native Ollama manifest has incomplete digests")
    result = [(manifest, MODEL_MANIFEST)]
    for value in sorted(set(digests)):
        match = DIGEST.fullmatch(value)
        if not match:
            raise ModelArchiveError(f"unsafe Ollama blob digest: {value!r}")
        name = f"sha256-{match.group(1)}"
        source = models_root / "blobs" / name
        if not source.is_file() or source.is_symlink():
            raise ModelArchiveError(f"qualified Ollama blob is missing or unsafe: {name}")
        result.append((source, PurePosixPath("models/blobs") / name))
    return result


def add_file(archive: tarfile.TarFile, source: Path, destination: PurePosixPath) -> None:
    information = tarfile.TarInfo(destination.as_posix())
    information.size = source.stat().st_size
    information.mode = 0o644
    information.uid = 0
    information.gid = 0
    information.mtime = 0
    with source.open("rb") as stream:
        archive.addfile(information, stream)


def create(models_root: Path, output: Path, expected_manifest_sha256: str) -> dict:
    models_root = models_root.expanduser().resolve()
    files = referenced_files(models_root)
    manifest_sha256 = sha256_file(files[0][0])
    if manifest_sha256 != expected_manifest_sha256:
        raise ModelArchiveError("native Ollama manifest differs from the qualified release")
    output = output.expanduser().resolve()
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with temporary.open("wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
                with tarfile.open(fileobj=compressed, mode="w") as archive:
                    for source, destination in files:
                        add_file(archive, source, destination)
        temporary.chmod(0o600)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "schema": "usl-ollama-portable-model-v1",
        "model": "usl-bge-m3:documents-20260824-rc1",
        "manifest_sha256": manifest_sha256,
        "archive_sha256": sha256_file(output),
        "archive_size": output.stat().st_size,
        "files": [destination.as_posix() for _source, destination in files],
    }


def inspect_archive(archive_path: Path) -> list[str]:
    try:
        with tarfile.open(archive_path, mode="r:gz") as archive:
            members = archive.getmembers()
            names = [member.name for member in members]
            if len(names) != len(set(names)):
                raise ModelArchiveError("portable model archive contains duplicate members")
            if any(
                not member.isfile()
                or member.issym()
                or member.islnk()
                or PurePosixPath(member.name).is_absolute()
                or ".." in PurePosixPath(member.name).parts
                for member in members
            ):
                raise ModelArchiveError("portable model archive contains unsafe members")
            if MODEL_MANIFEST.as_posix() not in names:
                raise ModelArchiveError("portable model archive is missing the qualified alias")
            manifest_member = archive.getmember(MODEL_MANIFEST.as_posix())
            manifest_stream = archive.extractfile(manifest_member)
            if manifest_stream is None:
                raise ModelArchiveError("portable model archive manifest is unreadable")
            manifest_payload = json.loads(manifest_stream.read())
            digests = [manifest_payload.get("config", {}).get("digest")]
            digests.extend(
                layer.get("digest") for layer in manifest_payload.get("layers", [])
            )
            expected = {MODEL_MANIFEST.as_posix()}
            for value in digests:
                match = DIGEST.fullmatch(value or "")
                if not match:
                    raise ModelArchiveError("portable model manifest has unsafe digests")
                expected.add(f"models/blobs/sha256-{match.group(1)}")
            if set(names) != expected:
                raise ModelArchiveError("portable model archive differs from its manifest")
            for name in sorted(expected - {MODEL_MANIFEST.as_posix()}):
                stream = archive.extractfile(archive.getmember(name))
                if stream is None:
                    raise ModelArchiveError(f"portable model blob is unreadable: {name}")
                digest = hashlib.sha256()
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
                if digest.hexdigest() != name.rsplit("-", 1)[-1]:
                    raise ModelArchiveError(f"portable model blob digest differs: {name}")
    except (OSError, KeyError, json.JSONDecodeError, tarfile.TarError) as error:
        raise ModelArchiveError("portable model archive is unreadable") from error
    return sorted(names)


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    subparsers = result.add_subparsers(dest="command", required=True)
    create_parser = subparsers.add_parser("create")
    create_parser.add_argument("--models-root", type=Path, required=True)
    create_parser.add_argument("--output", type=Path, required=True)
    create_parser.add_argument("--expected-manifest-sha256", required=True)
    inspect_parser = subparsers.add_parser("inspect")
    inspect_parser.add_argument("--archive", type=Path, required=True)
    return result


def main() -> None:
    args = parser().parse_args()
    try:
        if args.command == "create":
            value = create(
                args.models_root,
                args.output,
                args.expected_manifest_sha256,
            )
        else:
            value = {"files": inspect_archive(args.archive)}
    except ModelArchiveError as error:
        raise SystemExit(f"Ollama model archive rejected: {error}") from error
    print(json.dumps(value, sort_keys=True))


if __name__ == "__main__":
    main()
