"""Seal and verify the coordinated Odoo-Paperless Documents cohort."""

# ruff: noqa: EM101, T201 - operator CLI reports concise literal evidence.

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import tempfile
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = "usl-documents-release-cohort-v1"
REQUIRED_PATHS = (
    "odoo/odoo.dump",
    "odoo/odoo-filestore.tgz",
    "paperless/paperless.dump",
    "paperless/paperless-broker.tgz",
    "paperless/paperless-media.tgz",
    "paperless/paperless-data.tgz",
    "paperless/paperless-trash.tgz",
    "paperless/paperless-export.tgz",
    "embeddings/ollama-data.tgz",
    "embeddings/bge-model-manifest.json",
    "embeddings/vector-index-manifest.json",
    "mcp/source-identity.json",
    "mcp/image-identity.json",
    "mcp/runtime-smoke.json",
    "mcp/deployment-instructions.md",
    "mcp/rollback-instructions.md",
    "images/images.json",
    "evidence/release-identity.json",
    "evidence/accounting-control-totals.json",
    "evidence/documents-integrity.json",
    "evidence/archive-policy-ledger.json",
    "evidence/paperless-sanity.txt",
    "evidence/lexical-search-evidence.json",
    "evidence/semantic-evaluation.json",
    "evidence/hybrid-evaluation.json",
    "evidence/permission-evaluation.json",
    "evidence/mcp-acceptance.json",
    "evidence/gemini-security-tests.json",
    "evidence/clean-install.txt",
    "evidence/upgrade-idempotency.txt",
    "evidence/boundary-checks.txt",
    "evidence/recovery-rehearsal.txt",
    "configuration/non-secret-runtime.json",
    "configuration/required-secret-names.json",
    "configuration/restore-instructions.md",
    "configuration/rollback-instructions.md",
)
CONTROL_PATHS = frozenset({"manifest.json", "SHA256SUMS"})
FORBIDDEN_NAME_PARTS = frozenset(
    {
        ".env",
        "api-key",
        "api_key",
        "browser-session",
        "client-secret",
        "client_secret",
        "credential",
        "gemini-key",
        "gemini_key",
        "master-key",
        "master_key",
        "pocket-id-state",
        "private-key",
        "private_key",
        "refresh-token",
        "refresh_token",
    },
)


class BundleError(ValueError):
    """Raised when a cohort cannot be trusted or accepted."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def canonical_digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise BundleError(f"cannot read JSON: {path}") from error
    if not isinstance(value, dict):
        raise BundleError(f"JSON object required: {path}")
    return value


def atomic_json(path: Path, value: object) -> None:
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


def _validate_path(path: Path, root: Path) -> None:
    if path.is_symlink():
        raise BundleError(f"cohort artifacts may not be symlinks: {path}")
    relative = path.relative_to(root).as_posix()
    if relative == "configuration/required-secret-names.json":
        return
    lowered = relative.casefold()
    matched = sorted(part for part in FORBIDDEN_NAME_PARTS if part in lowered)
    if matched:
        raise BundleError(
            f"secret-shaped artifact name is forbidden ({', '.join(matched)}): {relative}",
        )


def artifact_manifest(root: Path) -> dict[str, dict[str, int | str]]:
    if root.is_symlink() or not root.is_dir():
        raise BundleError(f"cohort directory is missing or unsafe: {root}")
    result = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        _validate_path(path, root)
        if path.is_dir():
            continue
        if not path.is_file():
            raise BundleError(f"unsupported cohort artifact: {path}")
        relative = path.relative_to(root).as_posix()
        if relative in CONTROL_PATHS:
            continue
        result[relative] = {
            "sha256": sha256_file(path),
            "size": path.stat().st_size,
        }
    missing = sorted(set(REQUIRED_PATHS) - set(result))
    if missing:
        raise BundleError("required cohort artifacts are missing: " + ", ".join(missing))
    return result


def write_checksums(root: Path, artifacts: dict[str, dict[str, int | str]]) -> None:
    content = "".join(
        f"{metadata['sha256']}  {relative}\n"
        for relative, metadata in sorted(artifacts.items())
    )
    path = root / "SHA256SUMS"
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)


def seal(root: Path, identity_path: Path) -> dict:
    root = root.resolve()
    identity = read_json(identity_path.resolve())
    if identity.get("schema") != "usl-documents-release-identity-v1":
        raise BundleError("release identity schema is missing or unsupported")
    qualification = identity.get("qualification") or {}
    if qualification.get("status") not in {"passed", "partial"}:
        raise BundleError("release qualification must be explicitly passed or partial")
    artifacts = artifact_manifest(root)
    payload = {
        "schema": SCHEMA,
        "release_id": identity.get("release_id"),
        "created_at": datetime.now(UTC).isoformat(),
        "identity": identity,
        "identity_sha256": canonical_digest(identity),
        "artifacts": artifacts,
    }
    payload["manifest_sha256"] = canonical_digest(payload)
    atomic_json(root / "manifest.json", payload)
    write_checksums(root, artifacts)
    return payload


def verify(root: Path) -> dict:
    root = root.resolve()
    manifest = read_json(root / "manifest.json")
    if manifest.get("schema") != SCHEMA:
        raise BundleError("cohort schema is missing or unsupported")
    expected_manifest_sha = manifest.get("manifest_sha256")
    unsigned = dict(manifest)
    unsigned.pop("manifest_sha256", None)
    if expected_manifest_sha != canonical_digest(unsigned):
        raise BundleError("cohort manifest digest is invalid")
    identity = manifest.get("identity")
    if not isinstance(identity, dict):
        raise BundleError("cohort release identity is missing")
    if manifest.get("identity_sha256") != canonical_digest(identity):
        raise BundleError("cohort release identity digest is invalid")
    actual = artifact_manifest(root)
    if manifest.get("artifacts") != actual:
        changed = sorted(
            name
            for name in set(actual) | set(manifest.get("artifacts") or {})
            if actual.get(name) != (manifest.get("artifacts") or {}).get(name)
        )
        raise BundleError("cohort artifacts differ: " + ", ".join(changed))
    expected_checksums = "".join(
        f"{metadata['sha256']}  {relative}\n"
        for relative, metadata in sorted(actual.items())
    )
    try:
        actual_checksums = (root / "SHA256SUMS").read_text(encoding="utf-8")
    except OSError as error:
        raise BundleError("cohort SHA256SUMS is missing") from error
    if actual_checksums != expected_checksums:
        raise BundleError("cohort SHA256SUMS differs from the sealed manifest")
    return manifest


def _require_evidence_status(root: Path, relative: str) -> None:
    evidence = read_json(root / relative)
    if evidence.get("status") != "passed":
        raise BundleError(f"release evidence did not pass: {relative}")


def _require_text_status(root: Path, relative: str) -> None:
    try:
        content = (root / relative).read_text(encoding="utf-8")
    except OSError as error:
        raise BundleError(f"release evidence is unreadable: {relative}") from error
    if "status=passed" not in content.casefold():
        raise BundleError(f"release text evidence did not pass: {relative}")


def accept(root: Path) -> dict:
    manifest = verify(root)
    identity = manifest["identity"]
    qualification = identity.get("qualification") or {}
    if qualification.get("status") != "passed":
        reasons = qualification.get("blockers") or ["qualification is partial"]
        raise BundleError("cohort is not releasable: " + "; ".join(map(str, reasons)))
    required_zero = (
        "eligible_attachment_pending",
        "eligible_attachment_unresolved",
        "odoo_operations_failed",
        "odoo_operations_pending",
        "odoo_operations_processing",
        "paperless_active_tasks",
        "paperless_personal_profiles",
        "permission_failures",
        "unauthorized_results",
    )
    blockers = {
        name: qualification.get(name)
        for name in required_zero
        if qualification.get(name) != 0
    }
    if blockers:
        raise BundleError(f"cohort acceptance counters are nonzero: {blockers}")
    for relative in (
        "evidence/documents-integrity.json",
        "evidence/archive-policy-ledger.json",
        "evidence/permission-evaluation.json",
        "evidence/mcp-acceptance.json",
        "evidence/gemini-security-tests.json",
        "evidence/accounting-control-totals.json",
    ):
        _require_evidence_status(root, relative)
    for relative in (
        "evidence/clean-install.txt",
        "evidence/upgrade-idempotency.txt",
        "evidence/boundary-checks.txt",
        "evidence/recovery-rehearsal.txt",
    ):
        _require_text_status(root, relative)
    return manifest


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    subparsers = result.add_subparsers(dest="command", required=True)
    seal_parser = subparsers.add_parser("seal")
    seal_parser.add_argument("bundle", type=Path)
    seal_parser.add_argument("--identity", type=Path, required=True)
    for name in ("verify", "accept"):
        subparser = subparsers.add_parser(name)
        subparser.add_argument("bundle", type=Path)
    return result


def main() -> None:
    args = parser().parse_args()
    try:
        if args.command == "seal":
            manifest = seal(args.bundle, args.identity)
        elif args.command == "accept":
            manifest = accept(args.bundle)
        else:
            manifest = verify(args.bundle)
    except BundleError as error:
        raise SystemExit(f"Documents release cohort rejected: {error}") from error
    print(
        json.dumps(
            {
                "release_id": manifest.get("release_id"),
                "manifest_sha256": manifest.get("manifest_sha256"),
                "status": "accepted" if args.command == "accept" else "verified",
            },
            sort_keys=True,
        ),
    )


if __name__ == "__main__":
    main()
