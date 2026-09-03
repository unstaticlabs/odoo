#!/usr/bin/env python3
"""Seal and verify a private portable production-migration candidate."""

# This is an operator CLI with concise literal failures and intentional output.
# ruff: noqa: EM101, T201

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath

from migration import digests, portable_filestore

SCHEMA = "usl-production-migration-candidate-v2"
QUALIFICATION_SCHEMA = "usl-production-candidate-qualification-v2"
REQUIRED_ARTIFACTS = (
    "odoo.dump",
    "odoo-filestore.tgz",
    "paperless-export.tgz",
    "release-identity.json",
    "qualification.json",
    "evidence",
)
ARCHIVES = ("odoo-filestore.tgz", "paperless-export.tgz")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
IMAGE_DIGEST = re.compile(r"^.+@sha256:[0-9a-f]{64}$")


class CandidateError(ValueError):
    """Raised when portable migration evidence is incomplete or unsafe."""


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CandidateError(f"cannot read JSON: {path}") from error
    if not isinstance(value, dict):
        raise CandidateError(f"JSON object required: {path}")
    return value


def canonical_sha256(value: object) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


def atomic_json(path: Path, value: dict) -> None:
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


def _all_entries(root: Path) -> list[Path]:
    if root.is_symlink():
        raise CandidateError(f"candidate paths may not be symlinks: {root}")
    entries = [root]
    for directory, directories, files in os.walk(root, followlinks=False):
        parent = Path(directory)
        for name in [*directories, *files]:
            path = parent / name
            if path.is_symlink():
                raise CandidateError(
                    f"candidate paths may not be symlinks: {path}",
                )
            entries.append(path)
    return entries


def verify_private_modes(root: Path) -> None:
    for path in _all_entries(root):
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode & 0o077:
            raise CandidateError(
                f"candidate path is accessible to group/other: {path} ({mode:o})",
            )
        if path.is_dir() and mode != 0o700:
            raise CandidateError(
                f"candidate directory mode must be 0700: {path} ({mode:o})",
            )
        if path.is_file() and mode != 0o600:
            raise CandidateError(
                f"candidate file mode must be 0600: {path} ({mode:o})",
            )


def verify_safe_archive(path: Path) -> dict[str, int]:
    file_count = 0
    total_size = 0
    try:
        with tarfile.open(path, "r:*") as archive:
            for member in archive.getmembers():
                pure = PurePosixPath(member.name)
                if (
                    not member.name
                    or pure.is_absolute()
                    or ".." in pure.parts
                    or member.issym()
                    or member.islnk()
                    or member.isdev()
                    or member.isfifo()
                ):
                    raise CandidateError(
                        f"unsafe archive member in {path.name}: {member.name}",
                    )
                if member.isfile():
                    file_count += 1
                    total_size += member.size
    except (OSError, tarfile.TarError) as error:
        raise CandidateError(f"cannot inspect archive: {path}") from error
    if not file_count:
        raise CandidateError(f"archive contains no files: {path.name}")
    return {"file_count": file_count, "uncompressed_size": total_size}


def artifact_manifest(candidate_dir: Path) -> dict:
    result = {}
    for relative in REQUIRED_ARTIFACTS:
        path = candidate_dir / relative
        if not path.exists():
            raise CandidateError(f"required candidate artifact is missing: {relative}")
        digest, file_count, size = digests.tree_digest(path)
        result[relative] = {
            "file_count": file_count,
            "sha256": digest,
            "size": size,
        }
        if relative in ARCHIVES:
            result[relative]["archive"] = verify_safe_archive(path)
    dump = candidate_dir / "odoo.dump"
    if dump.read_bytes()[:5] != b"PGDMP":
        raise CandidateError("odoo.dump is not a PostgreSQL custom-format dump")
    inventory_path = candidate_dir / "evidence/odoo-filestore-attachments.tsv"
    if not inventory_path.is_file():
        raise CandidateError("Odoo filestore attachment inventory is missing")
    try:
        inventory = portable_filestore.read_inventory(inventory_path)
        portable_filestore.verify_archive(
            candidate_dir / "odoo-filestore.tgz",
            inventory,
        )
    except (
        OSError,
        tarfile.TarError,
        portable_filestore.FilestoreError,
    ) as error:
        raise CandidateError(str(error)) from error
    return result


def _passed(value: object) -> bool:
    return value == "passed" or (
        isinstance(value, dict) and value.get("status") == "passed"
    )


def verify_release_identity(identity: dict) -> str:
    if identity.get("schema") != "usl-release-identity-v1":
        raise CandidateError("release identity schema is missing or unsupported")
    recorded_identity_sha256 = identity.get("identity_sha256")
    unsigned_identity = {
        key: value for key, value in identity.items() if key != "identity_sha256"
    }
    if (
        not isinstance(recorded_identity_sha256, str)
        or not SHA256.fullmatch(recorded_identity_sha256)
        or canonical_sha256(unsigned_identity) != recorded_identity_sha256
    ):
        raise CandidateError("release identity integrity digest is missing or invalid")
    commit = identity.get("release_commit")
    if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise CandidateError("release identity has no exact commit")
    if identity.get("tree_clean") is not True:
        raise CandidateError("release identity was not built from a clean tree")
    upstream_commit = identity.get("upstream_saas_19_3_commit")
    if not isinstance(upstream_commit, str) or not re.fullmatch(
        r"[0-9a-f]{40}",
        upstream_commit,
    ):
        raise CandidateError("release identity has no exact saas-19.3 base")
    oca_digest = (identity.get("oca") or {}).get("bundle_sha256")
    if not isinstance(oca_digest, str) or not SHA256.fullmatch(oca_digest):
        raise CandidateError("release identity has no exact OCA bundle digest")
    action_risk_digest = identity.get("action_risk_policy_sha256")
    if not isinstance(action_risk_digest, str) or not SHA256.fullmatch(
        action_risk_digest,
    ):
        raise CandidateError("release identity has no exact action-risk policy digest")
    module_versions = identity.get("product_module_versions")
    if (
        not isinstance(module_versions, dict)
        or not module_versions
        or any(
            not isinstance(module, str)
            or not module
            or not isinstance(version, str)
            or not version
            for module, version in module_versions.items()
        )
    ):
        raise CandidateError("release identity has incomplete product module versions")
    image = identity.get("image") or {}
    labels = image.get("labels") or {}
    expected_labels = {
        "org.opencontainers.image.revision": commit,
        "com.unstaticlabs.odoo.oca-bundle-sha256": oca_digest,
        "com.unstaticlabs.odoo.action-risk-policy-sha256": action_risk_digest,
        "com.unstaticlabs.odoo.runtime": "distribution",
    }
    if any(labels.get(key) != value for key, value in expected_labels.items()):
        raise CandidateError("candidate image is not the Distribution image")
    repo_digests = image.get("repo_digests") or []
    immutable = sorted(digest for digest in repo_digests if IMAGE_DIGEST.fullmatch(digest))
    if not immutable:
        raise CandidateError("release image has no immutable registry digest")
    reference = image.get("reference")
    if not isinstance(reference, str) or not IMAGE_DIGEST.fullmatch(reference):
        raise CandidateError("release image reference is not an immutable digest")
    reference_digest = reference.rsplit("@", 1)[-1]
    if not any(item.rsplit("@", 1)[-1] == reference_digest for item in immutable):
        raise CandidateError("release image reference is absent from repository digests")
    return reference


def verify_qualification(
    qualification: dict,
    release_identity: dict,
    source_dump_sha256: str,
    source_filestore_sha256: str,
    migration_sha256: str,
) -> None:
    required_passes = (
        "action_risk",
        "accounting",
        "attachment_gate",
        "documents",
        "migration_boundary",
        "multi_company",
        "product_database_boundary",
        "source_gate",
    )
    if qualification.get("schema") != QUALIFICATION_SCHEMA:
        raise CandidateError("candidate qualification schema is missing or unsupported")
    if qualification.get("status") != "passed":
        raise CandidateError("candidate qualification did not pass")
    if qualification.get("purpose") != "production":
        raise CandidateError("candidate was not reconstructed for production")
    if qualification.get("profile") != "full":
        raise CandidateError("candidate did not use the full profile")
    if qualification.get("regulatory_live_guards") != "disabled":
        raise CandidateError("regulatory live guards were not disabled")
    for key in required_passes:
        if not _passed(qualification.get(key)):
            raise CandidateError(f"candidate {key} gate did not pass")
    for key in ("source_gate", "attachment_gate"):
        if (qualification.get(key) or {}).get("complete") is not True:
            raise CandidateError(f"candidate {key} is not complete")
    expected = {
        "release_commit": release_identity.get("release_commit"),
        "source_dump_sha256": source_dump_sha256,
        "source_filestore_sha256": source_filestore_sha256,
        "migration_sha256": migration_sha256,
    }
    for key, value in expected.items():
        if qualification.get(key) != value:
            raise CandidateError(
                f"candidate qualification {key} differs from compiled inputs",
            )
    if qualification.get("module_versions") != release_identity.get(
        "product_module_versions",
    ):
        raise CandidateError("candidate module versions differ from the release")
    if (qualification.get("action_risk") or {}).get(
        "policy_sha256",
    ) != release_identity.get("action_risk_policy_sha256"):
        raise CandidateError("candidate action-risk policy differs from the release")
    accounting = qualification.get("accounting") or {}
    accounting_performance = accounting.get("performance") or {}
    if (
        not isinstance(accounting.get("controls"), dict)
        or not accounting["controls"]
        or not isinstance(accounting_performance.get("schema"), str)
        or not accounting_performance["schema"]
        or not isinstance(accounting_performance.get("stages"), list)
        or not accounting_performance["stages"]
    ):
        raise CandidateError("candidate Accounting controls/timings are incomplete")
    documents = qualification.get("documents") or {}
    reconstruction = documents.get("reconstruction") or {}
    if (
        not isinstance(documents.get("controls"), dict)
        or not documents["controls"]
        or not isinstance(documents.get("paperless_document_count"), int)
        or documents["paperless_document_count"] < 0
        or not isinstance(documents.get("paperless_image_digest"), str)
        or not IMAGE_DIGEST.fullmatch(documents["paperless_image_digest"])
        or not isinstance(documents.get("ollama_image_digest"), str)
        or not IMAGE_DIGEST.fullmatch(documents["ollama_image_digest"])
        or not isinstance(reconstruction.get("ocr_submissions"), int)
        or reconstruction["ocr_submissions"] < 0
    ):
        raise CandidateError("candidate Documents controls are incomplete")
    sanitation = qualification.get("sanitation") or {}
    if sanitation.get("status") != "passed":
        raise CandidateError("candidate sanitation did not pass")
    for key in ("odoo", "paperless"):
        if (sanitation.get(key) or {}).get("status") != "passed":
            raise CandidateError(f"candidate {key} sanitation did not pass")
    if (sanitation.get("odoo") or {}).get("standard_neutralized") is not True:
        raise CandidateError("candidate did not run Odoo standard neutralization")
    filestore = qualification.get("odoo_filestore") or {}
    if filestore.get("status") != "passed":
        raise CandidateError("candidate Odoo filestore qualification did not pass")


def build_payload(
    candidate_dir: Path,
    root: Path,
    source_dir: Path | None,
) -> dict:
    release_identity = read_json(candidate_dir / "release-identity.json")
    image_digest = verify_release_identity(release_identity)
    qualification = read_json(candidate_dir / "qualification.json")
    if source_dir:
        source_dump_sha256 = digests.sha256_file(source_dir / "dump.sql")
        source_filestore_sha256 = digests.tree_digest(source_dir / "filestore")[0]
    else:
        source_dump_sha256 = (release_identity.get("source") or {}).get(
            "dump_sha256",
        )
        source_filestore_sha256 = qualification.get("source_filestore_sha256")
        if not isinstance(source_dump_sha256, str) or not SHA256.fullmatch(
            source_dump_sha256,
        ):
            raise CandidateError("release identity has no source dump digest")
        if not isinstance(source_filestore_sha256, str) or not SHA256.fullmatch(
            source_filestore_sha256,
        ):
            raise CandidateError("qualification has no source filestore digest")
    migration_sha256 = digests.migration_digest(root)
    if not SHA256.fullmatch(source_dump_sha256):  # pragma: no cover - defensive
        raise CandidateError("invalid source dump digest")
    if (release_identity.get("source") or {}).get("dump_sha256") != source_dump_sha256:
        raise CandidateError("release identity refers to another source dump")
    verify_qualification(
        qualification,
        release_identity,
        source_dump_sha256,
        source_filestore_sha256,
        migration_sha256,
    )
    try:
        inventory = portable_filestore.read_inventory(
            candidate_dir / "evidence/odoo-filestore-attachments.tsv",
        )
    except portable_filestore.FilestoreError as error:
        raise CandidateError(str(error)) from error
    filestore = qualification["odoo_filestore"]
    if filestore.get("distinct_store_file_count") != len(inventory):
        raise CandidateError("candidate filestore count differs from its inventory")
    if filestore.get("archive_sha256") != digests.sha256_file(
        candidate_dir / "odoo-filestore.tgz",
    ):
        raise CandidateError("candidate filestore digest differs from its qualification")
    payload = {
        "schema": SCHEMA,
        "created_at": datetime.now(UTC).isoformat(),
        "identity": {
            "image_digest": image_digest,
            "paperless_image_digest": qualification["documents"]["paperless_image_digest"],
            "ollama_image_digest": qualification["documents"]["ollama_image_digest"],
            "migration_sha256": migration_sha256,
            "oca_bundle_sha256": (release_identity.get("oca") or {}).get(
                "bundle_sha256",
            ),
            "action_risk_policy_sha256": release_identity.get(
                "action_risk_policy_sha256",
            ),
            "release_commit": release_identity["release_commit"],
            "source_dump_sha256": source_dump_sha256,
            "source_filestore_sha256": source_filestore_sha256,
            "upstream_saas_19_3_commit": release_identity.get(
                "upstream_saas_19_3_commit",
            ),
        },
        "artifacts": artifact_manifest(candidate_dir),
        "qualification_sha256": digests.sha256_file(
            candidate_dir / "qualification.json",
        ),
        "release_identity_sha256": digests.sha256_file(
            candidate_dir / "release-identity.json",
        ),
    }
    payload["fingerprint"] = canonical_sha256(payload)
    return payload


def seal(args: argparse.Namespace) -> dict:
    candidate_dir = args.candidate_dir.expanduser().resolve()
    root = args.root.expanduser().resolve()
    source_dir = args.source_dir.expanduser().resolve()
    verify_private_modes(candidate_dir)
    payload = build_payload(candidate_dir, root, source_dir)
    atomic_json(candidate_dir / "manifest.json", payload)
    return payload


def verify(args: argparse.Namespace) -> dict:
    candidate_dir = args.candidate_dir.expanduser().resolve()
    verify_private_modes(candidate_dir)
    manifest = read_json(candidate_dir / "manifest.json")
    if manifest.get("schema") != SCHEMA:
        raise CandidateError("candidate schema is missing or unsupported")
    actual = build_payload(
        candidate_dir,
        args.root.expanduser().resolve(),
        args.source_dir.expanduser().resolve() if args.source_dir else None,
    )
    # Creation time belongs to the sealed candidate, not this verification.
    actual["created_at"] = manifest.get("created_at")
    actual["fingerprint"] = canonical_sha256(
        {key: value for key, value in actual.items() if key != "fingerprint"},
    )
    if manifest != actual:
        raise CandidateError("candidate manifest or artifacts differ")
    if args.expected_fingerprint and manifest["fingerprint"] != args.expected_fingerprint:
        raise CandidateError("candidate fingerprint was not independently approved")
    return manifest


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    subparsers = result.add_subparsers(dest="command", required=True)
    for name in ("seal", "verify", "status"):
        subparser = subparsers.add_parser(name)
        subparser.add_argument("--root", required=True, type=Path)
        subparser.add_argument(
            "--source-dir",
            required=name == "seal",
            type=Path,
        )
        subparser.add_argument("--candidate-dir", required=True, type=Path)
        if name != "seal":
            subparser.add_argument("--expected-fingerprint")
    return result


def main() -> None:
    args = parser().parse_args()
    try:
        manifest = seal(args) if args.command == "seal" else verify(args)
    except (CandidateError, OSError, ValueError) as error:
        raise SystemExit(f"Migration candidate rejected: {error}") from error
    if args.command == "status":
        print(json.dumps({
            "candidate_dir": str(args.candidate_dir.expanduser().resolve()),
            "fingerprint": manifest["fingerprint"],
            "identity": manifest["identity"],
            "status": "ready",
        }, indent=2, sort_keys=True))
    else:
        print(manifest["fingerprint"])


if __name__ == "__main__":
    main()
