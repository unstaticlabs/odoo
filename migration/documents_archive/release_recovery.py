#!/usr/bin/env python3
"""Validate restored Documents state against a sealed release cohort."""

# ruff: noqa: EM101, T201 - operator CLI reports concise release evidence.

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import stat
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BUNDLE_SPEC = importlib.util.spec_from_file_location(
    "documents_release_bundle",
    ROOT / "migration/documents_archive/release_bundle.py",
)
release_bundle = importlib.util.module_from_spec(BUNDLE_SPEC)
assert BUNDLE_SPEC and BUNDLE_SPEC.loader
BUNDLE_SPEC.loader.exec_module(release_bundle)


class RecoveryError(ValueError):
    """Raised when an independently restored cohort differs from its source."""


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RecoveryError(f"cannot read JSON: {path}") from error
    if not isinstance(value, dict):
        raise RecoveryError(f"JSON object required: {path}")
    return value


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, stat.S_IRUSR | stat.S_IWUSR)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def build(
    manifest: dict,
    odoo: dict,
    paperless: dict,
    vector: dict,
    *,
    bge_manifest_sha256: str,
    tantivy_output: str,
    project: str,
    database: str,
) -> dict:
    identity = manifest.get("identity") or {}
    expected_documents = identity.get("documents") or {}
    expected_embedding = identity.get("embedding") or {}
    expected_model = expected_embedding.get("model") or {}
    expected_vector = expected_embedding.get("vector_index") or {}
    restored_documents = {
        "roots": odoo.get("root_count"),
        "active_links": odoo.get("active_link_count"),
        "versions": odoo.get("version_count"),
        "root_roles": odoo.get("root_role_counts"),
        "link_roles": odoo.get("link_role_counts"),
        "ledger_states": odoo.get("ledger_state_counts"),
    }
    checks = {
        "accounting_equal": odoo.get("accounting") == identity.get("accounting"),
        "bge_manifest_equal": (
            bge_manifest_sha256 == expected_model.get("manifest_sha256")
        ),
        "documents_equal": restored_documents == expected_documents,
        "paperless_equal": paperless == identity.get("paperless"),
        "stable_ids": all(
            paperless.get(name) == (identity.get("paperless") or {}).get(name)
            for name in (
                "document_count",
                "document_id_min",
                "document_id_max",
                "live_documents",
                "trash_documents",
            )
        ),
        "tantivy_noop": "Search index is up to date." in tantivy_output,
        "vector_equal": vector == expected_vector,
    }
    blockers = [name for name, passed in checks.items() if not passed]
    return {
        "schema": "usl-documents-recovery-rehearsal-v1",
        "status": "passed" if not blockers else "failed",
        "release_id": manifest.get("release_id"),
        "source_manifest_sha256": manifest.get("manifest_sha256"),
        "project": project,
        "database": database,
        "mass_ocr": False,
        "llm_rebuild": False,
        "model_download": False,
        "checks": checks,
        "blockers": blockers,
    }


def render_text(evidence: dict) -> str:
    lines = [
        f"status={evidence['status']}",
        f"release_id={evidence['release_id']}",
        f"source_manifest_sha256={evidence['source_manifest_sha256']}",
        f"project={evidence['project']}",
        f"database={evidence['database']}",
        "mass_ocr=false",
        "llm_rebuild=false",
        "model_download=false",
    ]
    lines.extend(
        f"{name}={'passed' if passed else 'failed'}"
        for name, passed in sorted(evidence["checks"].items())
    )
    return "\n".join(lines) + "\n"


def persist(evidence: dict, output: Path, text_output: Path) -> bool:
    atomic_write(
        output,
        json.dumps(evidence, indent=2, sort_keys=True) + "\n",
    )
    if evidence["status"] != "passed":
        return False
    atomic_write(text_output, render_text(evidence))
    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--odoo", type=Path, required=True)
    parser.add_argument("--paperless", type=Path, required=True)
    parser.add_argument("--vector", type=Path, required=True)
    parser.add_argument("--bge-manifest-sha256", required=True)
    parser.add_argument("--tantivy-output", type=Path, required=True)
    parser.add_argument("--project", required=True)
    parser.add_argument("--database", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--text-output", type=Path, required=True)
    args = parser.parse_args()
    try:
        manifest = release_bundle.verify(args.bundle)
        evidence = build(
            manifest,
            read_json(args.odoo),
            read_json(args.paperless),
            read_json(args.vector),
            bge_manifest_sha256=args.bge_manifest_sha256,
            tantivy_output=args.tantivy_output.read_text(encoding="utf-8"),
            project=args.project,
            database=args.database,
        )
    except (OSError, RecoveryError, release_bundle.BundleError) as error:
        raise SystemExit(f"Documents recovery evidence rejected: {error}") from error
    if not persist(evidence, args.output, args.text_output):
        raise SystemExit(
            "Documents recovery evidence rejected: "
            + ", ".join(evidence["blockers"]),
        )
    print(
        json.dumps(
            {
                "release_id": evidence["release_id"],
                "status": evidence["status"],
            },
            sort_keys=True,
        ),
    )


if __name__ == "__main__":
    main()
