#!/usr/bin/env python3
"""Build the cross-repository Documents release identity."""

# ruff: noqa: EM101, T201 - release CLI reports concise literal failures.

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.util
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PRODUCT_MODULES = (
    "rebuild_account_migration",
    "usl_accounting",
    "usl_documents",
    "usl_documents_accounting",
    "usl_expense_batch",
    "usl_home",
    "usl_locale",
    "usl_platform_billing",
    "usl_platform_billing_pocketid",
    "usl_pocketid",
    "usl_project",
    "usl_tese_accounting",
    "usl_tese_payroll",
)


class IdentityError(ValueError):
    """Raised when a release identity input is missing or inconsistent."""


def run(repository: Path, *command: str) -> str:
    completed = subprocess.run(
        command,
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode:
        detail = (completed.stderr or completed.stdout).strip()
        raise IdentityError(f"command failed ({' '.join(command)}): {detail}")
    return completed.stdout.strip()


def load_digest_module():
    spec = importlib.util.spec_from_file_location(
        "migration_release_digests",
        ROOT / "migration/digests.py",
    )
    if spec is None or spec.loader is None:
        raise IdentityError("migration digest module cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise IdentityError(f"cannot read JSON: {path}") from error
    if not isinstance(value, dict):
        raise IdentityError(f"JSON object required: {path}")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def module_versions() -> dict[str, str]:
    result = {}
    for name in PRODUCT_MODULES:
        path = ROOT / "custom-addons" / name / "__manifest__.py"
        try:
            value = ast.literal_eval(path.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, ValueError) as error:
            raise IdentityError(f"cannot read module manifest: {path}") from error
        version = value.get("version")
        if not isinstance(version, str) or not version:
            raise IdentityError(f"product module has no version: {name}")
        result[name] = version
    return result


def build(args: argparse.Namespace) -> dict:
    source = args.source_dir.resolve()
    dump = source / "dump.sql"
    filestore = source / "filestore"
    if not dump.is_file() or not filestore.is_dir():
        raise IdentityError(f"source package is incomplete: {source}")
    digests = load_digest_module()
    inventory = read_json(args.inventory)
    paperless = read_json(args.paperless)
    vector = read_json(args.vector)
    bge = read_json(args.bge)
    mcp = read_json(args.mcp)
    images = read_json(args.images)
    odoo_status = run(ROOT, "git", "status", "--porcelain=v1", "--untracked-files=all")
    mcp_repository = args.mcp_repository.resolve()
    mcp_status = run(
        mcp_repository,
        "git",
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    )
    odoo_commit = run(ROOT, "git", "rev-parse", "HEAD")
    mcp_commit = run(mcp_repository, "git", "rev-parse", "HEAD")
    if mcp.get("commit") != mcp_commit:
        raise IdentityError("MCP artifact identity does not match its checkout")
    counters = dict(inventory.get("qualification_counters") or {})
    counters.update(
        {
            "paperless_active_tasks": int(paperless.get("active_tasks", -1)),
            "paperless_personal_profiles": int(
                paperless.get("personal_profiles", -1),
            ),
            "unauthorized_results": int(args.unauthorized_results),
        },
    )
    blockers = list(inventory.get("blockers") or [])
    if odoo_status:
        blockers.append("Odoo checkout is dirty")
    if mcp_status:
        blockers.append("Odoo MCP checkout is dirty")
    if paperless.get("status") != "passed":
        blockers.append("Paperless finalization evidence did not pass")
    if vector.get("status") != "passed":
        blockers.append("vector-index finalization evidence did not pass")
    if bge.get("status") != "passed":
        blockers.append("BGE-M3 model evidence did not pass")
    if images.get("target_platform_status") != "passed":
        blockers.append("target-platform image evidence did not pass")
    blockers.extend(f"{name}={value}" for name, value in sorted(counters.items()) if value)
    blockers = list(dict.fromkeys(blockers))
    status = "passed" if not blockers else "partial"
    if status == "partial" and not args.allow_partial:
        raise IdentityError("release identity is incomplete: " + "; ".join(blockers))

    source_dump_sha = sha256_file(dump)
    source_filestore_sha, source_files, source_size = digests.tree_digest(filestore)
    identity = {
        "schema": "usl-documents-release-identity-v1",
        "release_id": args.release_id,
        "created_at": datetime.now(UTC).isoformat(),
        "target_platform": args.target_platform,
        "source": {
            "dump_sha256": source_dump_sha,
            "dump_size": dump.stat().st_size,
            "filestore_sha256": source_filestore_sha,
            "filestore_files": source_files,
            "filestore_size": source_size,
            "migration_code_sha256": digests.migration_digest(ROOT),
        },
        "git": {
            "odoo_branch": run(ROOT, "git", "branch", "--show-current"),
            "odoo_commit": odoo_commit,
            "odoo_mcp_branch": run(
                mcp_repository,
                "git",
                "branch",
                "--show-current",
            ),
            "odoo_mcp_commit": mcp_commit,
        },
        "product_module_versions": module_versions(),
        "images": images,
        "paperless": paperless,
        "embedding": {"model": bge, "vector_index": vector},
        "mcp": mcp,
        "documents": {
            "roots": inventory.get("root_count"),
            "active_links": inventory.get("active_link_count"),
            "versions": inventory.get("version_count"),
            "root_roles": inventory.get("root_role_counts"),
            "link_roles": inventory.get("link_role_counts"),
            "ledger_states": inventory.get("ledger_state_counts"),
        },
        "accounting": inventory.get("accounting"),
        "qualification": {
            "status": status,
            **counters,
            "blockers": blockers,
        },
    }
    identity["identity_sha256"] = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode(),
    ).hexdigest()
    return identity


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    result.add_argument("--source-dir", type=Path, required=True)
    result.add_argument("--inventory", type=Path, required=True)
    result.add_argument("--paperless", type=Path, required=True)
    result.add_argument("--vector", type=Path, required=True)
    result.add_argument("--bge", type=Path, required=True)
    result.add_argument("--mcp", type=Path, required=True)
    result.add_argument("--mcp-repository", type=Path, required=True)
    result.add_argument("--images", type=Path, required=True)
    result.add_argument("--release-id", required=True)
    result.add_argument("--target-platform", default="linux/amd64")
    result.add_argument("--unauthorized-results", type=int, default=0)
    result.add_argument("--allow-partial", action="store_true")
    result.add_argument("--output", type=Path, required=True)
    return result


def main() -> None:
    args = parser().parse_args()
    try:
        identity = build(args)
    except (IdentityError, OSError, ValueError) as error:
        raise SystemExit(f"Documents release identity rejected: {error}") from error
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(identity, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    args.output.chmod(0o600)
    print(json.dumps({"release_id": identity["release_id"], "status": identity["qualification"]["status"]}, sort_keys=True))


if __name__ == "__main__":
    main()
