#!/usr/bin/env python3
"""Seal, verify and admit an evolved post-work migration cohort."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import importlib.util
import json
import os
import re
import stat
import tarfile
import tempfile
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath

from scripts.distribution_release import ReleaseArtifactError, validate as validate_distribution_release


ROOT = Path(__file__).resolve().parents[2]
DOCUMENTS_SPEC = importlib.util.spec_from_file_location(
    "documents_release_bundle",
    ROOT / "migration/documents_archive/release_bundle.py",
)
documents_release_bundle = importlib.util.module_from_spec(DOCUMENTS_SPEC)
assert DOCUMENTS_SPEC and DOCUMENTS_SPEC.loader
DOCUMENTS_SPEC.loader.exec_module(documents_release_bundle)

SCHEMA = "usl-evolved-migration-cohort-v1"
STATE_SCHEMA = "usl-evolved-migration-admission-state-v1"
SHA256 = re.compile(r"[0-9a-f]{64}\Z")
GIT_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
ENV_NAME = re.compile(r"[A-Z][A-Z0-9_]*\Z")
SECRET_ASSIGNMENT = re.compile(
    rb"(?im)^\s*(?:[A-Z0-9_]+_)?(?:PASSWORD|SECRET_KEY|SECRET|ACCESS_TOKEN|"
    rb"REFRESH_TOKEN|TOKEN|CREDENTIAL|PRIVATE_KEY|API_KEY)"
    rb"\s*[:=]\s*(?!<|\$\{|REDACTED\b|EXTERNAL\b|REQUIRED\b)[\"']?[^\s\"']+",
)
PRIVATE_KEY_HEADER = re.compile(rb"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
URI_CREDENTIALS = re.compile(rb"://[^/\s:@]+:[^/\s@]+@")
CONTROL_PATHS = frozenset({"manifest.json", "SHA256SUMS"})
REQUIRED_PATHS = frozenset(
    {
        "documents/manifest.json",
        "documents/SHA256SUMS",
        "sign/step-ca.tgz",
        "sign/dss.tgz",
        "sign/evidence.tgz",
        "sign/manifest.json",
        "evidence/source-candidate-manifest.json",
        "evidence/release-identity.json",
        "evidence/distribution-release.json",
        "evidence/current-controls.json",
        "evidence/sanitation.json",
        "evidence/security-gates.json",
        "evidence/independent-restore.json",
        "configuration/non-secret-runtime.json",
        "configuration/required-secret-names.json",
        "configuration/restore-instructions.md",
        "configuration/admission-instructions.md",
        "configuration/rollback-instructions.md",
    },
)
FORBIDDEN_NAME_PARTS = frozenset(
    {
        ".env",
        "client-secret",
        "client_secret",
        "credential",
        "password",
        "pocket-id-state",
        "refresh-token",
        "refresh_token",
        "session-cookie",
    },
)
REQUIRED_ZERO_CONTROLS = (
    "outbound_queue",
    "documents_failed",
    "documents_pending",
    "documents_processing",
    "paperless_active_tasks",
    "paperless_failed_tasks",
    "sign_failed",
    "sign_pending",
    "unauthorized_results",
    "multi_company_leaks",
)
GATE_CHECKS = (
    "release_identity",
    "product_boundary",
    "accounting",
    "documents",
    "security",
    "multi_company",
    "sign",
    "queues",
    "regulatory_off",
)


class CohortError(ValueError):
    """Raised when evolved migration state cannot be trusted."""


def canonical_sha256(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CohortError(f"cannot read JSON evidence: {path}") from error
    if not isinstance(value, dict):
        raise CohortError(f"JSON object required: {path}")
    return value


def private_path(path: Path, *, directory: bool) -> None:
    if path.is_symlink() or (not path.is_dir() if directory else not path.is_file()):
        raise CohortError(f"unsafe cohort path: {path}")
    expected = 0o700 if directory else 0o600
    actual = stat.S_IMODE(path.stat().st_mode)
    if actual != expected:
        raise CohortError(f"cohort path must be mode {expected:04o}: {path} ({actual:04o})")


def validate_configuration(path: Path, relative: str) -> None:
    if not relative.startswith("configuration/"):
        return
    lowered = relative.casefold()
    if relative != "configuration/required-secret-names.json":
        matches = sorted(part for part in FORBIDDEN_NAME_PARTS if part in lowered)
        if matches:
            raise CohortError(
                f"secret-shaped transfer artifact is forbidden ({', '.join(matches)}): {relative}",
            )
    if relative == "configuration/non-secret-runtime.json":
        payload = read_json(path)

        def walk(value: object, prefix: str = "") -> None:
            if isinstance(value, dict):
                for key, item in value.items():
                    name = f"{prefix}.{key}" if prefix else str(key)
                    normalized = str(key).casefold().replace("-", "_")
                    forbidden = (
                        "password",
                        "secret",
                        "secret_key",
                        "token",
                        "access_token",
                        "refresh_token",
                        "credential",
                        "private_key",
                        "api_key",
                    )
                    if normalized in forbidden or any(
                        normalized.endswith(f"_{part}") for part in forbidden
                    ):
                        raise CohortError(
                            f"non-secret runtime contains a secret-shaped key: {name}",
                        )
                    walk(item, name)
            elif isinstance(value, list):
                for index, item in enumerate(value):
                    walk(item, f"{prefix}[{index}]")

        walk(payload)
    elif relative == "configuration/required-secret-names.json":
        payload = read_json(path)
        if set(payload) - {"schema", "required"}:
            raise CohortError("required-secret-names contains unsupported fields")
        required = payload.get("required")
        if (
            not isinstance(required, list)
            or not required
            or len(required) != len(set(required))
            or any(not isinstance(name, str) or not ENV_NAME.fullmatch(name) for name in required)
        ):
            raise CohortError(
                "required-secret-names must contain unique environment variable names",
            )
    content = path.read_bytes()
    if (
        PRIVATE_KEY_HEADER.search(content)
        or SECRET_ASSIGNMENT.search(content)
        or URI_CREDENTIALS.search(content)
    ):
        raise CohortError(f"transfer configuration contains a secret-shaped value: {relative}")


def artifact_manifest(root: Path) -> dict[str, dict[str, int | str]]:
    private_path(root, directory=True)
    result = {}
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        private_path(path, directory=path.is_dir())
        if path.is_dir():
            continue
        relative = path.relative_to(root).as_posix()
        if relative in CONTROL_PATHS:
            continue
        validate_configuration(path, relative)
        result[relative] = {"sha256": sha256_file(path), "size": path.stat().st_size}
    missing = sorted(REQUIRED_PATHS - set(result))
    if missing:
        raise CohortError("evolved cohort artifacts are missing: " + ", ".join(missing))
    return dict(sorted(result.items()))


def validate_identity_digest(identity: dict) -> None:
    expected = identity.get("identity_sha256")
    unsigned = dict(identity)
    unsigned.pop("identity_sha256", None)
    if not isinstance(expected, str) or expected != canonical_sha256(unsigned):
        raise CohortError("release identity digest is invalid")


def finite_decimal(value: object, label: str) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        raise CohortError(f"{label} is not a numeric control total")
    try:
        result = Decimal(str(value))
    except InvalidOperation as error:
        raise CohortError(f"{label} is not a numeric control total") from error
    if not result.is_finite():
        raise CohortError(f"{label} is not a finite control total")
    return result


def validate_sign_state(root: Path, release_identity_sha256: str) -> dict:
    sign_root = root / "sign"
    private_path(sign_root, directory=True)
    manifest_path = sign_root / "manifest.json"
    private_path(manifest_path, directory=False)
    sign = read_json(manifest_path)
    if (
        sign.get("schema") != "usl-sign-transfer-state-v1"
        or sign.get("status") != "passed"
    ):
        raise CohortError("Sign transfer state did not pass")
    if sign.get("release_identity_sha256") != release_identity_sha256:
        raise CohortError("Sign transfer state refers to another evolved release")
    for name, archive_name in (
        ("step_ca", "step-ca.tgz"),
        ("dss", "dss.tgz"),
        ("evidence", "evidence.tgz"),
    ):
        entry = sign.get(name) or {}
        archive_path = sign_root / archive_name
        private_path(archive_path, directory=False)
        if (
            entry.get("complete") is not True
            or type(entry.get("file_count")) is not int
            or entry.get("file_count") <= 0
            or entry.get("archive_sha256") != sha256_file(archive_path)
            or entry.get("archive_size") != archive_path.stat().st_size
        ):
            raise CohortError(f"Sign transfer state is incomplete: {name}")
    return sign


def component_identity(root: Path, *, accept_documents: bool) -> dict:
    root = root.expanduser().resolve()
    private_path(root, directory=True)
    candidate_path = root / "evidence/source-candidate-manifest.json"
    private_path(candidate_path, directory=False)
    candidate = read_json(candidate_path)
    if candidate.get("schema") != "usl-production-migration-candidate-v2":
        raise CohortError("Online-source candidate schema is unsupported")
    candidate_fingerprint = candidate.get("fingerprint")
    if not isinstance(candidate_fingerprint, str) or not SHA256.fullmatch(candidate_fingerprint):
        raise CohortError("Online-source candidate fingerprint is invalid")

    release_path = root / "evidence/release-identity.json"
    private_path(release_path, directory=False)
    release = read_json(release_path)
    if release.get("schema") != "usl-release-identity-v1" or release.get("tree_clean") is not True:
        raise CohortError("evolved cohort release identity is not clean or supported")
    validate_identity_digest(release)
    release_commit = release.get("release_commit")
    if not isinstance(release_commit, str) or not GIT_COMMIT.fullmatch(release_commit):
        raise CohortError("evolved cohort release commit is invalid")

    distribution_path = root / "evidence/distribution-release.json"
    private_path(distribution_path, directory=False)
    try:
        distribution = validate_distribution_release(
            read_json(distribution_path),
            commit=release_commit,
        )
    except ReleaseArtifactError as error:
        raise CohortError(f"Distribution release artifact was rejected: {error}") from error

    private_path(root / "documents", directory=True)
    try:
        documents = (
            documents_release_bundle.accept(root / "documents")
            if accept_documents
            else documents_release_bundle.verify(root / "documents")
        )
    except documents_release_bundle.BundleError as error:
        raise CohortError(f"nested Documents cohort was rejected: {error}") from error
    documents_identity = documents.get("identity") or {}
    if (documents_identity.get("git") or {}).get("odoo_commit") != release_commit:
        raise CohortError("Documents cohort and evolved release commit differ")
    documents_manifest_sha256 = documents.get("manifest_sha256")
    if (
        not isinstance(documents_manifest_sha256, str)
        or not SHA256.fullmatch(documents_manifest_sha256)
    ):
        raise CohortError("Documents cohort manifest digest is invalid")
    validate_sign_state(root, release["identity_sha256"])

    identity = {
        "candidate_fingerprint": candidate_fingerprint,
        "source_candidate_manifest_sha256": sha256_file(candidate_path),
        "release_identity_sha256": release["identity_sha256"],
        "distribution_release_sha256": sha256_file(distribution_path),
        "release_commit": release_commit,
        "documents_manifest_sha256": documents_manifest_sha256,
        "sign_manifest_sha256": sha256_file(root / "sign/manifest.json"),
    }
    identity["component_fingerprint"] = canonical_sha256(identity)
    return identity


def validate_inputs(root: Path, *, accept_documents: bool) -> dict:
    components = component_identity(root, accept_documents=accept_documents)
    candidate_fingerprint = components["candidate_fingerprint"]
    candidate_manifest_sha256 = components["source_candidate_manifest_sha256"]
    release_identity_sha256 = components["release_identity_sha256"]
    distribution_release_sha256 = components["distribution_release_sha256"]
    documents_manifest_sha256 = components["documents_manifest_sha256"]

    controls = read_json(root / "evidence/current-controls.json")
    if controls.get("schema") != "usl-evolved-transition-controls-v1":
        raise CohortError("evolved current-controls schema is unsupported")
    if controls.get("status") != "passed":
        raise CohortError("evolved current controls did not pass")
    if controls.get("source_candidate_fingerprint") != candidate_fingerprint:
        raise CohortError("current controls refer to another Online-source candidate")
    if controls.get("source_candidate_manifest_sha256") != candidate_manifest_sha256:
        raise CohortError("current controls refer to another Online-source candidate manifest")
    if controls.get("release_identity_sha256") != release_identity_sha256:
        raise CohortError("current controls refer to another evolved release")
    if controls.get("distribution_release_sha256") != distribution_release_sha256:
        raise CohortError("current controls refer to another Distribution release")
    if controls.get("documents_manifest_sha256") != documents_manifest_sha256:
        raise CohortError("current controls refer to another Documents cohort")
    accounting = controls.get("accounting") or {}
    posted_debit = finite_decimal(accounting.get("posted_debit"), "posted debit")
    posted_credit = finite_decimal(accounting.get("posted_credit"), "posted credit")
    if (
        accounting.get("balanced") is not True
        or posted_debit != posted_credit
    ):
        raise CohortError("evolved Accounting controls are not balanced")
    blockers = {
        name: controls.get(name)
        for name in REQUIRED_ZERO_CONTROLS
        if type(controls.get(name)) is not int or controls.get(name) != 0
    }
    if blockers:
        raise CohortError(f"evolved current controls have blockers: {blockers}")
    current_controls_sha256 = canonical_sha256(controls)

    sanitation = read_json(root / "evidence/sanitation.json")
    required_sanitation = (
        sanitation.get("schema") == "usl-evolved-cohort-sanitation-v1",
        sanitation.get("status") == "passed",
        sanitation.get("source_mutated") is False,
        sanitation.get("odoo_clone_sanitized") is True,
        sanitation.get("paperless_clone_sanitized") is True,
        sanitation.get("transient_identities_removed") is True,
        sanitation.get("transfer_configuration_contains_secrets") is False,
        sanitation.get("source_candidate_fingerprint") == candidate_fingerprint,
        sanitation.get("release_identity_sha256") == release_identity_sha256,
        sanitation.get("distribution_release_sha256") == distribution_release_sha256,
        sanitation.get("documents_manifest_sha256") == documents_manifest_sha256,
        sanitation.get("current_controls_sha256") == current_controls_sha256,
    )
    if not all(required_sanitation):
        raise CohortError("clone-only sanitation evidence is incomplete")

    security = read_json(root / "evidence/security-gates.json")
    if (
        security.get("schema") != "usl-evolved-cohort-security-gates-v1"
        or security.get("status") != "passed"
    ):
        raise CohortError("evolved cohort security gates did not pass")
    if security.get("release_identity_sha256") != release_identity_sha256:
        raise CohortError("security gates refer to another evolved release")
    if security.get("distribution_release_sha256") != distribution_release_sha256:
        raise CohortError("security gates refer to another Distribution release")
    if (
        security.get("source_candidate_fingerprint") != candidate_fingerprint
        or security.get("documents_manifest_sha256") != documents_manifest_sha256
        or security.get("current_controls_sha256") != current_controls_sha256
    ):
        raise CohortError("security gates refer to another evolved cohort component")
    missing_gates = [name for name in GATE_CHECKS if security.get(name) is not True]
    if missing_gates:
        raise CohortError("evolved security gates are incomplete: " + ", ".join(missing_gates))

    recovery = read_json(root / "evidence/independent-restore.json")
    recovery_checks = (
        recovery.get("schema") == "usl-evolved-cohort-independent-restore-v1",
        recovery.get("status") == "passed",
        recovery.get("fresh_volumes") is True,
        recovery.get("source_project_distinct") is True,
        recovery.get("accounting_equal") is True,
        recovery.get("documents_equal") is True,
        recovery.get("paperless_equal") is True,
        recovery.get("sign_equal") is True,
        recovery.get("vector_equal") is True,
        recovery.get("tantivy_equal") is True,
        type(recovery.get("ocr_submissions")) is int,
        recovery.get("ocr_submissions") == 0,
        type(recovery.get("reingestion_submissions")) is int,
        recovery.get("reingestion_submissions") == 0,
        recovery.get("vector_rebuild") is False,
        recovery.get("model_download") is False,
        recovery.get("source_candidate_fingerprint") == candidate_fingerprint,
        recovery.get("release_identity_sha256") == release_identity_sha256,
        recovery.get("distribution_release_sha256") == distribution_release_sha256,
        recovery.get("documents_manifest_sha256") == documents_manifest_sha256,
        recovery.get("component_fingerprint") == components["component_fingerprint"],
        recovery.get("current_controls_sha256") == current_controls_sha256,
    )
    if not all(recovery_checks):
        raise CohortError("independent evolved-cohort restore evidence is incomplete")
    return {
        "candidate_fingerprint": candidate_fingerprint,
        "source_candidate_manifest_sha256": candidate_manifest_sha256,
        "release_identity_sha256": release_identity_sha256,
        "distribution_release_sha256": distribution_release_sha256,
        "release_commit": components["release_commit"],
        "documents_manifest_sha256": documents_manifest_sha256,
        "sign_manifest_sha256": components["sign_manifest_sha256"],
        "component_fingerprint": components["component_fingerprint"],
        "current_controls_sha256": current_controls_sha256,
    }


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    private_path(path.parent, directory=True)
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


def atomic_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    private_path(path.parent, directory=True)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def write_checksums(root: Path, artifacts: dict) -> None:
    content = "".join(
        f"{metadata['sha256']}  {relative}\n"
        for relative, metadata in sorted(artifacts.items())
    )
    atomic_text(root / "SHA256SUMS", content)


def seal(root: Path) -> dict:
    root = root.expanduser().resolve()
    existing_controls = [name for name in CONTROL_PATHS if (root / name).exists()]
    if existing_controls:
        raise CohortError(
            "evolved cohort is already sealed or has unsafe control files: "
            + ", ".join(sorted(existing_controls)),
        )
    artifacts = artifact_manifest(root)
    identity = validate_inputs(root, accept_documents=False)
    payload = {
        "schema": SCHEMA,
        "created_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "identity": identity,
        "artifacts": artifacts,
    }
    payload["cohort_fingerprint"] = canonical_sha256(payload)
    atomic_json(root / "manifest.json", payload)
    write_checksums(root, artifacts)
    return payload


def verify(root: Path) -> dict:
    root = root.expanduser().resolve()
    manifest_path = root / "manifest.json"
    private_path(manifest_path, directory=False)
    manifest = read_json(manifest_path)
    if manifest.get("schema") != SCHEMA:
        raise CohortError("evolved cohort manifest schema is unsupported")
    fingerprint = manifest.get("cohort_fingerprint")
    unsigned = dict(manifest)
    unsigned.pop("cohort_fingerprint", None)
    if not isinstance(fingerprint, str) or fingerprint != canonical_sha256(unsigned):
        raise CohortError("evolved cohort fingerprint is invalid")
    artifacts = artifact_manifest(root)
    if artifacts != manifest.get("artifacts"):
        raise CohortError("evolved cohort artifacts differ from the sealed manifest")
    expected_checksums = "".join(
        f"{metadata['sha256']}  {relative}\n"
        for relative, metadata in sorted(artifacts.items())
    )
    checksums_path = root / "SHA256SUMS"
    private_path(checksums_path, directory=False)
    if checksums_path.read_text(encoding="utf-8") != expected_checksums:
        raise CohortError("evolved cohort SHA256SUMS differs")
    identity = validate_inputs(root, accept_documents=False)
    if identity != manifest.get("identity"):
        raise CohortError("evolved cohort identity inputs differ")
    return manifest


def accept(root: Path) -> dict:
    manifest = verify(root)
    accepted_identity = validate_inputs(root.expanduser().resolve(), accept_documents=True)
    if accepted_identity != manifest.get("identity"):
        raise CohortError("accepted evolved cohort identity differs")
    return manifest


def read_state(path: Path, fingerprint: str) -> dict:
    if path.is_symlink():
        raise CohortError("admission state must not be a symbolic link")
    if path.exists():
        private_path(path, directory=False)
        state = read_json(path)
    else:
        state = {
            "schema": STATE_SCHEMA,
            "cohort_fingerprint": fingerprint,
            "status": "new",
            "reset_allowed": False,
            "history": [],
        }
    if state.get("schema") != STATE_SCHEMA or state.get("cohort_fingerprint") != fingerprint:
        raise CohortError("admission state belongs to another evolved cohort")
    if (
        state.get("status")
        not in {"new", "preflight", "restored", "configured", "gated", "admitted"}
        or state.get("reset_allowed") is not False
        or not isinstance(state.get("history"), list)
    ):
        raise CohortError("admission state is malformed or reset-capable")
    return state


def validate_transition_evidence(path: Path, fingerprint: str, action: str) -> None:
    private_path(path, directory=False)
    value = read_json(path)
    if value.get("cohort_fingerprint") != fingerprint or value.get("status") != "passed":
        raise CohortError(f"{action} evidence did not pass for this evolved cohort")
    expected_schema = {
        "configure": "usl-evolved-cohort-configuration-v1",
        "gate": "usl-evolved-cohort-production-gate-v1",
        "admit": "usl-evolved-cohort-admission-v1",
    }.get(action)
    if value.get("schema") != expected_schema:
        raise CohortError(f"{action} evidence schema is unsupported")
    required = {
        "configure": (
            "identity_reconfigured",
            "secrets_external",
            "pocket_state_not_transferred",
            "outbound_disabled",
        ),
        "gate": GATE_CHECKS,
        "admit": (
            "backup_restore_proven",
            "ingress_ready",
            "go_no_go_approved",
            "rollback_ready",
        ),
    }.get(action, ())
    missing = [name for name in required if value.get(name) is not True]
    if missing:
        raise CohortError(f"{action} evidence is incomplete: {', '.join(missing)}")


def transition(path: Path, fingerprint: str, action: str, evidence: Path | None) -> dict:
    if not SHA256.fullmatch(fingerprint):
        raise CohortError("evolved cohort fingerprint is invalid")
    state = read_state(path, fingerprint)
    current = state["status"]
    expected = {
        "preflight": {"new", "preflight"},
        "restore": {"preflight"},
        "configure": {"restored", "configured"},
        "gate": {"configured", "gated"},
        "admit": {"gated"},
    }
    if current not in expected.get(action, set()):
        raise CohortError(f"cannot {action} evolved cohort in {current!r} state")
    if action in {"configure", "gate", "admit"}:
        if evidence is None:
            raise CohortError(f"{action} requires private passed evidence")
        validate_transition_evidence(evidence, fingerprint, action)
    target = {
        "preflight": "preflight",
        "restore": "restored",
        "configure": "configured",
        "gate": "gated",
        "admit": "admitted",
    }[action]
    state["status"] = target
    state["history"].append(
        {
            "action": action,
            "at": datetime.now(UTC).replace(microsecond=0).isoformat(),
            **({"evidence_sha256": sha256_file(evidence)} if evidence is not None else {}),
        },
    )
    atomic_json(path, state)
    return state


def require_state(path: Path, fingerprint: str, expected: str) -> dict:
    state = read_state(path, fingerprint)
    if state.get("status") != expected:
        raise CohortError(
            f"evolved cohort state must be {expected!r}, found {state.get('status')!r}",
        )
    return state


def safe_extract(archive_path: Path, destination: Path) -> None:
    if destination.exists():
        raise CohortError(f"Sign restore destination already exists: {destination}")
    destination.mkdir(parents=True, mode=0o700)
    destination.chmod(0o700)
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            members = archive.getmembers()
            if any(
                member.issym()
                or member.islnk()
                or PurePosixPath(member.name).is_absolute()
                or ".." in PurePosixPath(member.name).parts
                or not (member.isdir() or member.isfile())
                for member in members
            ):
                raise CohortError(f"unsafe Sign archive members: {archive_path}")
            archive.extractall(destination, filter="data")
            for path in sorted(destination.rglob("*")):
                path.chmod(0o700 if path.is_dir() else 0o600)
    except (OSError, tarfile.TarError) as error:
        raise CohortError(f"cannot restore Sign archive: {archive_path}") from error


def archive_directory(source: Path, output: Path) -> dict:
    source = source.expanduser().resolve()
    private_path(source, directory=True)
    paths = sorted(source.rglob("*"), key=lambda item: item.relative_to(source).as_posix())
    if any(path.is_symlink() or not (path.is_dir() or path.is_file()) for path in paths):
        raise CohortError(f"Sign state contains unsafe filesystem objects: {source}")
    file_count = sum(path.is_file() for path in paths)
    if file_count == 0:
        raise CohortError(f"Sign state is empty: {source}")
    output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    private_path(output.parent, directory=True)
    if output.exists() or output.is_symlink():
        raise CohortError(f"Sign archive already exists: {output}")
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{output.name}.", dir=output.parent)
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        with temporary.open("wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
                with tarfile.open(fileobj=compressed, mode="w") as archive:
                    for path in paths:
                        relative = path.relative_to(source).as_posix()
                        information = tarfile.TarInfo(relative)
                        information.uid = 0
                        information.gid = 0
                        information.mtime = 0
                        if path.is_dir():
                            information.type = tarfile.DIRTYPE
                            information.mode = 0o700
                            archive.addfile(information)
                        else:
                            information.size = path.stat().st_size
                            information.mode = 0o600
                            with path.open("rb") as stream:
                                archive.addfile(information, stream)
        temporary.chmod(0o600)
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "complete": True,
        "file_count": file_count,
        "archive_sha256": sha256_file(output),
        "archive_size": output.stat().st_size,
    }


def capture_sign(
    bundle: Path,
    step_ca: Path,
    dss: Path,
    evidence: Path,
    release_identity: Path,
    distribution_release: Path,
) -> dict:
    bundle = bundle.expanduser().resolve()
    private_path(bundle, directory=True)
    sign_root = bundle / "sign"
    if sign_root.exists():
        raise CohortError("Sign transfer directory already exists")
    sign_root.mkdir(mode=0o700)
    release_identity = release_identity.expanduser().resolve()
    private_path(release_identity, directory=False)
    release = read_json(release_identity)
    if (
        release.get("schema") != "usl-release-identity-v1"
        or release.get("tree_clean") is not True
    ):
        raise CohortError("Sign capture release identity is dirty or unsupported")
    validate_identity_digest(release)
    release_commit = release.get("release_commit")
    if not isinstance(release_commit, str) or not GIT_COMMIT.fullmatch(release_commit):
        raise CohortError("Sign capture release commit is invalid")
    distribution_release = distribution_release.expanduser().resolve()
    private_path(distribution_release, directory=False)
    try:
        validate_distribution_release(
            read_json(distribution_release),
            commit=release_commit,
        )
    except ReleaseArtifactError as error:
        raise CohortError(f"Distribution release artifact was rejected: {error}") from error
    distribution_target = bundle / "evidence/distribution-release.json"
    if distribution_target.exists() or distribution_target.is_symlink():
        raise CohortError("Distribution release evidence already exists")
    distribution_target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    distribution_target.parent.chmod(0o700)
    distribution_target.write_bytes(distribution_release.read_bytes())
    distribution_target.chmod(0o600)
    payload = {
        "schema": "usl-sign-transfer-state-v1",
        "status": "passed",
        "release_identity_sha256": release["identity_sha256"],
        "step_ca": archive_directory(step_ca, sign_root / "step-ca.tgz"),
        "dss": archive_directory(dss, sign_root / "dss.tgz"),
        "evidence": archive_directory(evidence, sign_root / "evidence.tgz"),
    }
    atomic_json(sign_root / "manifest.json", payload)
    return payload


def extract_sign_archives(root: Path, destination: Path) -> None:
    destination = destination.expanduser().resolve()
    if destination.exists():
        raise CohortError("Sign restore root must be fresh")
    destination.mkdir(parents=True, mode=0o700)
    destination.chmod(0o700)
    try:
        for archive_name, directory_name in (
            ("step-ca.tgz", "step-ca"),
            ("dss.tgz", "dss"),
            ("evidence.tgz", "evidence"),
        ):
            safe_extract(root / "sign" / archive_name, destination / directory_name)
    except Exception:
        # Preserve a partial restore for diagnosis; never overwrite or retry it.
        raise


def restore_sign(root: Path, destination: Path, expected_fingerprint: str) -> dict:
    manifest = accept(root)
    if manifest["cohort_fingerprint"] != expected_fingerprint:
        raise CohortError("Sign restore was not confirmed with the accepted cohort fingerprint")
    extract_sign_archives(root, destination)
    return {
        "schema": "usl-sign-cohort-restore-v1",
        "status": "passed",
        "cohort_fingerprint": expected_fingerprint,
        "destination": str(destination.expanduser().resolve()),
    }


def restore_sign_components(
    root: Path,
    destination: Path,
    expected_fingerprint: str,
) -> dict:
    identity = component_identity(root, accept_documents=True)
    if identity["component_fingerprint"] != expected_fingerprint:
        raise CohortError(
            "Sign rehearsal was not confirmed with the accepted component fingerprint",
        )
    extract_sign_archives(root, destination)
    return {
        "schema": "usl-sign-component-rehearsal-v1",
        "status": "passed",
        "component_fingerprint": expected_fingerprint,
        "destination": str(destination.expanduser().resolve()),
    }


def restore_sign_runtime(
    root: Path,
    step_ca: Path,
    dss: Path,
    evidence: Path,
    expected_fingerprint: str,
) -> dict:
    manifest = accept(root)
    if manifest["cohort_fingerprint"] != expected_fingerprint:
        raise CohortError("Sign runtime restore was not confirmed with the cohort fingerprint")
    destinations = (
        ("step-ca.tgz", step_ca),
        ("dss.tgz", dss),
        ("evidence.tgz", evidence),
    )
    for archive_name, destination in destinations:
        safe_extract(root / "sign" / archive_name, destination.expanduser().resolve())
    return {
        "schema": "usl-sign-runtime-restore-v1",
        "status": "passed",
        "cohort_fingerprint": expected_fingerprint,
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    subparsers = result.add_subparsers(dest="command", required=True)
    for command in ("seal", "verify", "accept"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("bundle", type=Path)
    inspect_parser = subparsers.add_parser("inspect-components")
    inspect_parser.add_argument("bundle", type=Path)
    transition_parser = subparsers.add_parser("transition")
    transition_parser.add_argument("--state", type=Path, required=True)
    transition_parser.add_argument("--fingerprint", required=True)
    transition_parser.add_argument(
        "--action",
        choices=("preflight", "restore", "configure", "gate", "admit"),
        required=True,
    )
    transition_parser.add_argument("--evidence", type=Path)
    state_parser = subparsers.add_parser("require-state")
    state_parser.add_argument("--state", type=Path, required=True)
    state_parser.add_argument("--fingerprint", required=True)
    state_parser.add_argument("--expected", required=True)
    restore_parser = subparsers.add_parser("restore-sign")
    restore_parser.add_argument("bundle", type=Path)
    restore_parser.add_argument("--destination", type=Path, required=True)
    restore_parser.add_argument("--confirm", required=True)
    rehearsal_parser = subparsers.add_parser("restore-sign-components")
    rehearsal_parser.add_argument("bundle", type=Path)
    rehearsal_parser.add_argument("--destination", type=Path, required=True)
    rehearsal_parser.add_argument("--confirm", required=True)
    runtime_restore_parser = subparsers.add_parser("restore-sign-runtime")
    runtime_restore_parser.add_argument("bundle", type=Path)
    runtime_restore_parser.add_argument("--step-ca", type=Path, required=True)
    runtime_restore_parser.add_argument("--dss", type=Path, required=True)
    runtime_restore_parser.add_argument("--evidence", type=Path, required=True)
    runtime_restore_parser.add_argument("--confirm", required=True)
    capture_parser = subparsers.add_parser("capture-sign")
    capture_parser.add_argument("bundle", type=Path)
    capture_parser.add_argument("--step-ca", type=Path, required=True)
    capture_parser.add_argument("--dss", type=Path, required=True)
    capture_parser.add_argument("--evidence", type=Path, required=True)
    capture_parser.add_argument("--release-identity", type=Path, required=True)
    capture_parser.add_argument("--distribution-release", type=Path, required=True)
    return result


def main() -> None:
    args = parser().parse_args()
    try:
        if args.command == "seal":
            value = seal(args.bundle)
        elif args.command == "verify":
            value = verify(args.bundle)
        elif args.command == "accept":
            value = accept(args.bundle)
        elif args.command == "inspect-components":
            value = component_identity(args.bundle, accept_documents=True)
        elif args.command == "transition":
            value = transition(args.state, args.fingerprint, args.action, args.evidence)
        elif args.command == "require-state":
            value = require_state(args.state, args.fingerprint, args.expected)
        elif args.command == "capture-sign":
            value = capture_sign(
                args.bundle,
                args.step_ca,
                args.dss,
                args.evidence,
                args.release_identity,
                args.distribution_release,
            )
        elif args.command == "restore-sign-components":
            value = restore_sign_components(args.bundle, args.destination, args.confirm)
        elif args.command == "restore-sign-runtime":
            value = restore_sign_runtime(
                args.bundle,
                args.step_ca,
                args.dss,
                args.evidence,
                args.confirm,
            )
        else:
            value = restore_sign(args.bundle, args.destination, args.confirm)
    except (CohortError, OSError, tarfile.TarError) as error:
        raise SystemExit(f"Evolved migration cohort rejected: {error}") from error
    if args.command in {"seal", "verify", "accept"}:
        value = {
            "schema": value["schema"],
            "status": "accepted" if args.command == "accept" else args.command,
            "cohort_fingerprint": value["cohort_fingerprint"],
            "release_commit": value["identity"]["release_commit"],
        }
    print(json.dumps(value, sort_keys=True))


if __name__ == "__main__":
    main()
