#!/usr/bin/env python3
"""Inventory and gate the complete Odoo Online source truth.

This is a repository migration tool, not an Odoo add-on.  It reads the restored
source with PostgreSQL read-only transactions and writes only ignored evidence
under ``artifacts/migration/private``.
"""

from __future__ import annotations

import argparse
import csv
import fnmatch
import hashlib
import io
import json
import os
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONTRACT = ROOT / "migration/source_truth/coverage.json"
DEFAULT_SOURCE_DIR = Path(
    os.environ.get("USL_ONLINE_DUMP_DIR", "/Users/valentin/Code/odoo/usl-online-dump"),
)
DEFAULT_ARTIFACTS = ROOT / "artifacts/migration/private"
CONTRACT_SCHEMA = "usl-source-truth-coverage-v1"
INVENTORY_SCHEMA = "usl-source-truth-inventory-v1"
CURRENT_DISTRIBUTION_SCOPES = {
    "accounting",
    "credential_state",
    "documents",
    "hr",
    "identity",
    "native_reference",
    "platform_billing",
    "product_master",
    "projects",
    "tese_payroll",
}


class AuditError(RuntimeError):
    """An unsafe or invalid migration-audit condition."""


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha1_file(path: Path) -> str:
    # Odoo records historical filestore checksums as SHA-1.  This validates
    # compatibility with that existing contract; it is not used for security.
    digest = hashlib.sha1()  # noqa: S324
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_package(source_dir: Path) -> dict[str, Any]:
    source_dir = source_dir.resolve()
    dump_path = source_dir / "dump.sql"
    filestore = source_dir / "filestore"
    if not dump_path.is_file() or not filestore.is_dir():
        raise AuditError(f"dump.sql or filestore is missing under {source_dir}")
    return {
        "root": str(source_dir),
        "dump_path": str(dump_path),
        "dump_size": dump_path.stat().st_size,
        "dump_sha256": sha256_file(dump_path),
        "filestore": str(filestore),
    }


def load_contract(path: Path) -> dict[str, Any]:
    contract = json.loads(path.read_text(encoding="utf-8"))
    if contract.get("schema") != CONTRACT_SCHEMA:
        raise AuditError(f"unsupported coverage contract schema in {path}")
    scopes = contract.get("scopes") or {}
    if not scopes:
        raise AuditError("coverage contract has no scopes")
    for rule_type in ("model_rules", "table_rules"):
        for rule in contract.get(rule_type) or []:
            if rule.get("scope") not in scopes or not rule.get("match"):
                raise AuditError(f"invalid {rule_type} entry: {rule!r}")
    return contract


def classify(name: str, rules: list[dict[str, str]]) -> str | None:
    for rule in rules:
        if fnmatch.fnmatchcase(name, rule["match"]):
            return rule["scope"]
    return None


class SourceDatabase:
    def __init__(self, container: str, database: str, user: str):
        self.container = container
        self.database = database
        self.user = user

    def csv(self, sql: str) -> list[dict[str, str]]:
        command = [
            "docker",
            "exec",
            "-i",
            "-e",
            "PGOPTIONS=-c default_transaction_read_only=on",
            self.container,
            "psql",
            "-X",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            self.user,
            "-d",
            self.database,
            "--csv",
            "-f",
            "-",
        ]
        result = subprocess.run(
            command,
            input=sql,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode:
            raise AuditError(result.stderr.strip() or "source database query failed")
        return list(csv.DictReader(io.StringIO(result.stdout)))

    def assert_source(self) -> dict[str, str]:
        rows = self.csv(
            "COPY (SELECT current_database() AS database, "
            "current_setting('transaction_read_only') AS read_only, "
            "(SELECT count(*) FROM ir_module_module WHERE state = 'installed') AS installed_modules) "
            "TO STDOUT WITH CSV HEADER;",
        )
        if len(rows) != 1 or rows[0]["read_only"] != "on":
            raise AuditError("source connection is not read-only")
        return rows[0]

    def _counts(self, table_names: list[str]) -> dict[str, int]:
        if not table_names:
            return {}
        selects = []
        for table_name in table_names:
            literal = table_name.replace("'", "''")
            identifier = table_name.replace('"', '""')
            selects.append(
                f"SELECT '{literal}'::text AS table_name, count(*)::bigint "
                f'AS record_count FROM "{identifier}"',
            )
        rows = self.csv(
            "COPY (" + " UNION ALL ".join(selects) + ") TO STDOUT WITH CSV HEADER;",
        )
        return {row["table_name"]: int(row["record_count"]) for row in rows}

    def models(self) -> list[dict[str, Any]]:
        rows = self.csv(
            r"""
COPY (
    SELECT model.model, replace(model.model, '.', '_') AS table_name,
           COALESCE((
               SELECT data.module
                 FROM ir_model_data data
                WHERE data.model = 'ir.model' AND data.res_id = model.id
                ORDER BY CASE WHEN data.module = '__export__' THEN 1 ELSE 0 END,
                         data.module, data.id
                LIMIT 1
           ), '') AS owner_module,
           model.transient,
           to_regclass('public.' || quote_ident(replace(model.model, '.', '_')))
               IS NOT NULL AS table_exists
      FROM ir_model model
     ORDER BY model.model
) TO STDOUT WITH CSV HEADER;
""",
        )
        counts = self._counts(
            [row["table_name"] for row in rows if row["table_exists"] == "t"],
        )
        for row in rows:
            row["transient"] = row["transient"] == "t"
            row["table_exists"] = row["table_exists"] == "t"
            row["record_count"] = counts.get(row["table_name"], 0)
        return rows

    def tables(self) -> list[dict[str, Any]]:
        rows = self.csv(
            r"""
COPY (SELECT tablename AS table_name FROM pg_tables
       WHERE schemaname = 'public' ORDER BY tablename)
TO STDOUT WITH CSV HEADER;
""",
        )
        counts = self._counts([row["table_name"] for row in rows])
        for row in rows:
            row["record_count"] = counts[row["table_name"]]
        return rows

    def attachments(self) -> list[dict[str, Any]]:
        rows = self.csv(
            r"""
COPY (
    SELECT id, COALESCE(store_fname, '') AS store_fname,
           COALESCE(checksum, '') AS checksum,
           COALESCE(file_size, 0) AS file_size,
           COALESCE(type, '') AS type,
           COALESCE(mimetype, '') AS mimetype,
           COALESCE(res_model, '') AS res_model,
           COALESCE(res_id, 0) AS res_id,
           db_datas IS NOT NULL AS has_db_data,
           COALESCE(url, '') AS url
      FROM ir_attachment
     ORDER BY id
) TO STDOUT WITH CSV HEADER;
""",
        )
        for row in rows:
            row["id"] = int(row["id"])
            row["file_size"] = int(row["file_size"] or 0)
            row["res_id"] = int(row["res_id"] or 0)
            row["has_db_data"] = row["has_db_data"] == "t"
        return rows


def verify_filestore(
    filestore: Path,
    attachments: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    filestore = filestore.resolve()
    referenced: set[str] = set()
    errors: list[dict[str, Any]] = []
    checked = 0
    checked_bytes = 0
    for row in attachments:
        stored_name = row["store_fname"]
        if not stored_name:
            if row["type"] == "binary" and not row["has_db_data"]:
                errors.append({"attachment_id": row["id"], "error": "binary has no stored data"})
            continue
        path = (filestore / stored_name).resolve()
        if filestore not in path.parents:
            errors.append({"attachment_id": row["id"], "error": "unsafe filestore path"})
            continue
        referenced.add(path.relative_to(filestore).as_posix())
        if not path.is_file():
            errors.append({"attachment_id": row["id"], "error": "filestore object is missing"})
            continue
        checked += 1
        size = path.stat().st_size
        checked_bytes += size
        if row["file_size"] and size != row["file_size"]:
            errors.append({"attachment_id": row["id"], "error": "file size differs"})
        if row["checksum"]:
            digest = sha1_file(path)
            if digest != row["checksum"]:
                errors.append({"attachment_id": row["id"], "error": "SHA-1 differs"})

    all_files = sorted(path for path in filestore.rglob("*") if path.is_file())
    rollup = hashlib.sha256()
    all_bytes = 0
    orphan_files = []
    for path in all_files:
        relative = path.relative_to(filestore).as_posix()
        size = path.stat().st_size
        all_bytes += size
        digest = sha256_file(path)
        rollup.update(f"{relative}\0{size}\0{digest}\n".encode())
        if relative not in referenced:
            orphan_files.append(relative)
    summary = {
        "attachment_rows": len(attachments),
        "checked_stored_objects": checked,
        "checked_stored_bytes": checked_bytes,
        "filestore_files": len(all_files),
        "filestore_bytes": all_bytes,
        "filestore_rollup_sha256": rollup.hexdigest(),
        "unreferenced_filestore_files": len(orphan_files),
        "unreferenced_paths": orphan_files,
    }
    return summary, errors


def build_inventory(
    package: dict[str, Any],
    database: SourceDatabase,
    contract: dict[str, Any],
) -> dict[str, Any]:
    database_status = database.assert_source()
    models = database.models()
    tables = database.tables()
    attachments = database.attachments()
    filestore, attachment_errors = verify_filestore(
        Path(package["filestore"]), attachments,
    )
    model_tables = {row["table_name"] for row in models if row["table_exists"]}
    populated_models = []
    unclassified_models = []
    scope_counts: Counter[str] = Counter()
    for row in models:
        scope = (
            "native_reference"
            if row["transient"]
            else classify(row["model"], contract["model_rules"])
        )
        row["scope"] = scope
        if row["record_count"]:
            populated_models.append(row)
            if scope:
                scope_counts[scope] += row["record_count"]
            elif not row["transient"]:
                unclassified_models.append(row["model"])
    relation_tables = []
    unclassified_tables = []
    for row in tables:
        if row["table_name"] in model_tables or not row["record_count"]:
            continue
        row["scope"] = classify(row["table_name"], contract["table_rules"])
        relation_tables.append(row)
        if not row["scope"]:
            unclassified_tables.append(row["table_name"])
    incomplete_scopes = sorted(
        scope
        for scope, count in scope_counts.items()
        if count and contract["scopes"][scope]["status"] != "implemented"
    )
    blocking = {
        "attachment_integrity_errors": attachment_errors,
        "unclassified_populated_models": sorted(unclassified_models),
        "unclassified_populated_tables": sorted(unclassified_tables),
        "incomplete_populated_scopes": incomplete_scopes,
    }
    return {
        "schema": INVENTORY_SCHEMA,
        "generated_at": datetime.now(UTC).isoformat(),
        "source": {
            **package,
            "database": database_status["database"],
            "installed_modules": int(database_status["installed_modules"]),
        },
        "contract": contract,
        "summary": {
            "database_models": len(models),
            "populated_models": len(populated_models),
            "database_tables": len(tables),
            "populated_relation_or_unmapped_tables": len(relation_tables),
            "scope_record_counts": dict(sorted(scope_counts.items())),
            "complete": not any(blocking.values()),
        },
        "models": models,
        "relation_or_unmapped_tables": relation_tables,
        "filestore": filestore,
        "blocking": blocking,
    }


def write_inventory(inventory: dict[str, Any], root: Path) -> Path:
    snapshot = f"source-{inventory['source']['dump_sha256'][:12]}"
    destination = root / snapshot / "source-truth-inventory.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(inventory, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return destination


def print_summary(
    inventory: dict[str, Any],
    destination: Path,
    *,
    show_full_status: bool = True,
) -> None:
    summary = inventory["summary"]
    blocking = inventory["blocking"]
    print(f"Source dump: {inventory['source']['dump_sha256']}")
    print(
        f"Inventory: {summary['populated_models']} populated models; "
        f"{summary['populated_relation_or_unmapped_tables']} populated relation/unmapped tables",
    )
    print(
        f"Filestore: {inventory['filestore']['filestore_files']} files; "
        f"{inventory['filestore']['checked_stored_objects']} referenced objects verified",
    )
    print(f"Evidence: {destination}")
    if not show_full_status:
        return
    if summary["complete"]:
        print("PASS: every populated source scope is classified, implemented, and integrity-clean.")
        return
    print("BLOCKED: complete source reconstruction is not yet proven.")
    for key, value in blocking.items():
        if value:
            preview = value[:12] if isinstance(value, list) else value
            print(f"- {key}: {preview}")


def current_distribution_blocking(inventory: dict[str, Any]) -> dict[str, Any]:
    """Return blockers for reconstructing the product scopes shipped today.

    Source-wide incomplete scopes stay visible in the inventory and continue to
    block the stricter ``gate`` command. Their future product stages must not
    prevent a deterministic rebuild of the already implemented distribution.
    """
    full = inventory["blocking"]
    incomplete_current = sorted(
        set(full["incomplete_populated_scopes"]) & CURRENT_DISTRIBUTION_SCOPES,
    )
    missing_contract = sorted(
        scope
        for scope in CURRENT_DISTRIBUTION_SCOPES
        if inventory["contract"]["scopes"].get(scope, {}).get("status")
        != "implemented"
    )
    return {
        "attachment_integrity_errors": full["attachment_integrity_errors"],
        "unclassified_populated_models": full["unclassified_populated_models"],
        "unclassified_populated_tables": full["unclassified_populated_tables"],
        "incomplete_current_distribution_scopes": incomplete_current,
        "missing_current_distribution_contracts": missing_contract,
    }


def print_current_distribution_summary(inventory: dict[str, Any]) -> None:
    blocking = current_distribution_blocking(inventory)
    if any(blocking.values()):
        print("BLOCKED: the current Odoo distribution cannot be reconstructed safely.")
        for key, value in blocking.items():
            if value:
                preview = value[:12] if isinstance(value, list) else value
                print(f"- {key}: {preview}")
        return
    deferred = sorted(
        set(inventory["blocking"]["incomplete_populated_scopes"])
        - CURRENT_DISTRIBUTION_SCOPES,
    )
    print("PASS: current distribution scopes are classified and integrity-clean.")
    if deferred:
        print(f"DEFERRED: source-wide product scopes still open: {deferred}")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("inventory", "product-gate", "gate"))
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--contract", type=Path, default=DEFAULT_CONTRACT)
    parser.add_argument("--artifacts", type=Path, default=DEFAULT_ARTIFACTS)
    parser.add_argument(
        "--container",
        default=os.environ.get("MIGRATION_SOURCE_CONTAINER"),
    )
    parser.add_argument(
        "--database",
        default=os.environ.get(
            "MIGRATION_SOURCE_DATABASE",
            "odoo_online_source_saas_19_2",
        ),
    )
    parser.add_argument("--user", default=os.environ.get("MIGRATION_SOURCE_USER", "odoo"))
    return parser.parse_args(argv)


def resolve_source_container(explicit: str | None) -> str:
    if explicit:
        return explicit
    project = os.environ.get("ACCOUNTING_COMPAT_COMPOSE_PROJECT") or os.environ.get(
        "COMPOSE_PROJECT_NAME",
    )
    if not project:
        raise AuditError(
            "set MIGRATION_SOURCE_CONTAINER or an isolated "
            "ACCOUNTING_COMPAT_COMPOSE_PROJECT; no default shared Docker project is used",
        )
    result = subprocess.run(
        ["docker", "compose", "-p", project, "ps", "-q", "accounting-source-db"],
        text=True,
        capture_output=True,
        check=False,
    )
    container = result.stdout.strip()
    if result.returncode or not container or "\n" in container:
        raise AuditError(
            f"the source database service is not uniquely running in Compose project {project}",
        )
    return container


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        package = source_package(args.source_dir)
        contract = load_contract(args.contract)
        inventory = build_inventory(
            package,
            SourceDatabase(
                resolve_source_container(args.container),
                args.database,
                args.user,
            ),
            contract,
        )
        destination = write_inventory(inventory, args.artifacts)
        print_summary(
            inventory,
            destination,
            show_full_status=args.command != "product-gate",
        )
        if args.command == "product-gate":
            print_current_distribution_summary(inventory)
            return 1 if any(current_distribution_blocking(inventory).values()) else 0
        return 1 if args.command == "gate" and not inventory["summary"]["complete"] else 0
    except (AuditError, OSError, ValueError) as error:
        print(f"Migration source-truth audit failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
