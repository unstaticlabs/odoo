#!/usr/bin/env python3
"""Create and verify private, immutable QA reconstruction seeds."""

# ruff: noqa: EM101, T201

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import tempfile
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = "usl-qa-reconstruction-seed-v2"
MIGRATION_INPUTS = (
    "Dockerfile",
    "accounting_compat",
    "compose.yaml",
    "compose.external-pocket-id.yaml",
    "compose.pocket-id.yaml",
    "compose.preprod.yaml",
    "custom-addons",
    "deploy/documents",
    "migration",
    "scripts/accounting-compat",
    "scripts/accounting-restore",
    "scripts/attachment-ledger",
    "scripts/documents-restore",
    "scripts/hr-restore",
    "scripts/identity-restore",
    "scripts/migration-source-truth",
    "scripts/migration-candidate",
    "scripts/migration_candidate.py",
    "scripts/platform-billing-restore",
    "scripts/paperless_seed_sanitize.py",
    "scripts/portable_filestore.py",
    "scripts/product-restore",
    "scripts/project-restore",
    "scripts/sign-restore",
    "scripts/qa-seed",
    "scripts/qa-environment",
    "scripts/qa_seed.py",
    "scripts/production-cutover",
    "scripts/production_cutover.py",
    "scripts/release_identity.py",
    "scripts/odoo/production_admission_policy.py",
    "scripts/odoo/production_record_admission.py",
    "scripts/odoo/production_side_effect_boundary.py",
    "scripts/odoo/qa_seed_sanitize.py",
    "scripts/target-finalize",
    "scripts/target-reconstruct",
    "scripts/tese-restore",
)
IGNORED_PARTS = {
    ".git",
    "__pycache__",
    "private",
    ".ruff_cache",
    ".pytest_cache",
}
IGNORED_SUFFIXES = {".pyc", ".log"}
REQUIRED_ARTIFACTS = (
    "odoo.dump",
    "odoo-filestore.tgz",
    "paperless-export",
    "runtime.json",
)


class SeedError(ValueError):
    """Raised when a seed cannot be trusted."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def iter_files(path: Path) -> list[Path]:
    if path.is_symlink():
        raise SeedError(f"seed artifacts may not be symlinks: {path}")
    if path.is_file():
        return [path]
    files = []
    for item in path.rglob("*"):
        if item.is_symlink():
            raise SeedError(f"seed artifacts may not be symlinks: {item}")
        if item.is_file():
            files.append(item)
    return sorted(files, key=lambda item: item.relative_to(path).as_posix())


def tree_digest(path: Path) -> tuple[str, int, int]:
    digest = hashlib.sha256()
    total_size = 0
    files = iter_files(path)
    for item in files:
        relative = item.relative_to(path).as_posix().encode()
        content_sha = sha256_file(item).encode()
        size = item.stat().st_size
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(size.to_bytes(8, "big"))
        digest.update(content_sha)
        total_size += size
    return digest.hexdigest(), len(files), total_size


def _migration_files(root: Path) -> list[Path]:
    files = []
    for relative in MIGRATION_INPUTS:
        path = root / relative
        if not path.exists():
            raise SeedError(f"migration identity input is missing: {relative}")
        candidates = [path] if path.is_file() else path.rglob("*")
        files.extend(
            candidate
            for candidate in candidates
            if candidate.is_file()
            and not set(candidate.relative_to(root).parts).intersection(IGNORED_PARTS)
            and candidate.suffix not in IGNORED_SUFFIXES
        )
    return sorted(set(files), key=lambda item: item.relative_to(root).as_posix())


def migration_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in _migration_files(root):
        relative = path.relative_to(root).as_posix().encode()
        content = path.read_bytes()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    return digest.hexdigest()


def compose_runtime(config: dict, image_ids: dict[str, str]) -> dict:
    services = config.get("services") or {}
    required = (
        "db",
        "odoo",
        "paperless-db",
        "paperless-webserver",
        "paperless-gotenberg",
        "paperless-tika",
    )
    missing = [name for name in required if name not in services]
    if missing:
        raise SeedError(f"Compose services are missing: {', '.join(missing)}")
    environment = services["paperless-webserver"].get("environment") or {}
    images = {
        name: {
            "reference": services[name].get("image") or "",
            "image_id": image_ids.get(name) or "",
        }
        for name in required
    }
    if any(not item["reference"] for item in images.values()):
        raise SeedError("every qualified service must resolve an image reference")
    if any(not item["image_id"] for item in images.values()):
        raise SeedError("every qualified service image must exist locally")
    return {
        "images": images,
        "paperless": {
            "api": "v10",
            "barcodes": str(
                environment.get("PAPERLESS_CONSUMER_ENABLE_BARCODES", ""),
            ),
            "ocr_language": str(environment.get("PAPERLESS_OCR_LANGUAGE", "")),
            "ocr_user_args": str(environment.get("PAPERLESS_OCR_USER_ARGS", "")),
        },
    }


def read_json(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SeedError(f"cannot read JSON: {path}") from error


def identity(root: Path, source_dump: Path, runtime: dict) -> dict:
    filestore = source_dump.parent / "filestore"
    if not filestore.is_dir():
        raise SeedError(f"source filestore is missing: {filestore}")
    return {
        "migration_sha256": migration_digest(root),
        "runtime": runtime,
        "source_dump_sha256": sha256_file(source_dump),
        "source_filestore_sha256": tree_digest(filestore)[0],
    }


def identity_fingerprint(value: dict) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


def artifact_manifest(seed_dir: Path) -> dict:
    result = {}
    for relative in REQUIRED_ARTIFACTS:
        path = seed_dir / relative
        if not path.exists():
            raise SeedError(f"required seed artifact is missing: {relative}")
        digest, file_count, size = tree_digest(path)
        result[relative] = {
            "file_count": file_count,
            "sha256": digest,
            "size": size,
        }
    return result


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def validate_qualification(qualification: dict) -> None:
    required = {
        "accounting": "passed",
        "documents": "passed",
        "migration_boundary": "passed",
        "product_database_boundary": "passed",
        "profile": "full",
        "regulatory_live_guards": "disabled",
        "status": "passed",
    }
    for key, expected in required.items():
        value = qualification.get(key)
        if isinstance(expected, str) and isinstance(value, dict):
            value = value.get("status")
        if value != expected:
            raise SeedError(
                f"seed qualification {key} is {value!r}, expected {expected!r}",
            )
    modules = qualification.get("module_versions")
    if not isinstance(modules, dict) or not modules or any(
        not isinstance(name, str)
        or not name
        or not isinstance(version, str)
        or not version
        for name, version in modules.items()
    ):
        raise SeedError("seed qualification has no exact module versions")
    accounting = qualification["accounting"]
    performance = accounting.get("performance") or {}
    if (
        not isinstance(accounting.get("controls"), dict)
        or not accounting["controls"]
        or not isinstance(performance.get("stages"), list)
        or not performance["stages"]
        or not performance.get("schema")
    ):
        raise SeedError("seed qualification has incomplete Accounting controls/timings")
    documents = qualification["documents"]
    if (
        not isinstance(documents.get("controls"), dict)
        or not documents["controls"]
        or not isinstance(documents.get("paperless_document_count"), int)
        or documents["paperless_document_count"] < 0
    ):
        raise SeedError("seed qualification has incomplete Documents controls")


def seal(args: argparse.Namespace) -> None:
    seed_dir = args.seed_dir.resolve()
    runtime = read_json(args.runtime_json)
    seed_identity = identity(args.root.resolve(), args.source_dump.resolve(), runtime)
    qualification = read_json(args.qualification_json)
    validate_qualification(qualification)
    payload = {
        "schema": SCHEMA,
        "created_at": datetime.now(UTC).isoformat(),
        "created_from_commit": args.commit,
        "identity": seed_identity,
        "fingerprint": identity_fingerprint(seed_identity),
        "artifacts": artifact_manifest(seed_dir),
        "qualification": qualification,
    }
    atomic_json(seed_dir / "manifest.json", payload)
    print(payload["fingerprint"])


def verify(args: argparse.Namespace) -> dict:
    seed_dir = args.seed_dir.resolve()
    manifest = read_json(seed_dir / "manifest.json")
    if manifest.get("schema") != SCHEMA:
        raise SeedError("seed schema is missing or unsupported")
    if manifest.get("created_from_commit") != args.commit:
        raise SeedError("seed release commit differs from the current checkout")
    expected_identity = identity(
        args.root.resolve(),
        args.source_dump.resolve(),
        read_json(args.runtime_json),
    )
    if manifest.get("identity") != expected_identity:
        changed = sorted(
            key
            for key, value in expected_identity.items()
            if manifest.get("identity", {}).get(key) != value
        )
        raise SeedError("seed identity differs: " + ", ".join(changed))
    if manifest.get("fingerprint") != identity_fingerprint(expected_identity):
        raise SeedError("seed fingerprint is invalid")
    actual_artifacts = artifact_manifest(seed_dir)
    if manifest.get("artifacts") != actual_artifacts:
        changed = sorted(
            name
            for name, value in actual_artifacts.items()
            if manifest.get("artifacts", {}).get(name) != value
        )
        raise SeedError("seed artifacts differ: " + ", ".join(changed))
    qualification = manifest.get("qualification") or {}
    validate_qualification(qualification)
    return manifest


def runtime_command(args: argparse.Namespace) -> None:
    image_ids = {}
    for value in args.image_id:
        name, separator, image_id = value.partition("=")
        if not separator or not name or not image_id or name in image_ids:
            raise SeedError(f"invalid or duplicate image ID: {value}")
        image_ids[name] = image_id
    config = read_json(args.compose_config)
    atomic_json(args.output, compose_runtime(config, image_ids))


def status(args: argparse.Namespace) -> None:
    manifest = verify(args)
    result = {
        "artifacts": manifest["artifacts"],
        "created_at": manifest["created_at"],
        "created_from_commit": manifest["created_from_commit"],
        "fingerprint": manifest["fingerprint"],
        "seed_dir": str(args.seed_dir.resolve()),
        "status": "ready",
    }
    print(json.dumps(result, indent=2, sort_keys=True))


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    subparsers = result.add_subparsers(dest="command", required=True)

    runtime_parser = subparsers.add_parser("runtime")
    runtime_parser.add_argument("--compose-config", type=Path, required=True)
    runtime_parser.add_argument("--image-id", action="append", default=[])
    runtime_parser.add_argument("--output", type=Path, required=True)

    for name in ("verify", "status"):
        subparser = subparsers.add_parser(name)
        subparser.add_argument("--root", type=Path, required=True)
        subparser.add_argument("--seed-dir", type=Path, required=True)
        subparser.add_argument("--source-dump", type=Path, required=True)
        subparser.add_argument("--runtime-json", type=Path, required=True)
        subparser.add_argument("--commit", required=True)

    seal_parser = subparsers.add_parser("seal")
    seal_parser.add_argument("--root", type=Path, required=True)
    seal_parser.add_argument("--seed-dir", type=Path, required=True)
    seal_parser.add_argument("--source-dump", type=Path, required=True)
    seal_parser.add_argument("--runtime-json", type=Path, required=True)
    seal_parser.add_argument("--qualification-json", type=Path, required=True)
    seal_parser.add_argument("--commit", required=True)
    return result


def main() -> None:
    args = parser().parse_args()
    try:
        if args.command == "runtime":
            runtime_command(args)
        elif args.command == "seal":
            seal(args)
        elif args.command == "verify":
            manifest = verify(args)
            print(manifest["fingerprint"])
        else:
            status(args)
    except SeedError as error:
        raise SystemExit(f"QA seed rejected: {error}") from error


if __name__ == "__main__":
    main()
