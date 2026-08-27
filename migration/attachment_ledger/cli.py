#!/usr/bin/env python3
"""Build the lossless disposition ledger for every source attachment.

The ledger is evidence, not an importer.  It binds every ``ir.attachment`` row
to the immutable source package, verifies its bytes, records every required
downstream action, and refuses to report completion while an action is still
owned by an incomplete migration scope.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from migration.source_truth.cli import (  # noqa: E402
    AuditError,
    SourceDatabase,
    default_source_dir,
    resolve_source_container,
    source_package,
    verify_filestore,
)

SCHEMA = "usl-source-attachment-ledger-v1"
DEFAULT_SOURCE_DIR = default_source_dir()
DEFAULT_ARTIFACTS = ROOT / "artifacts/migration/private"

# These stages already restore the attachment and prove target bytes by source
# identity.  A row may additionally be a Documents original, in which case it
# intentionally receives both operational and archive actions.
RESTORED_BUSINESS_MODELS = {
    "account.asset": "accounting",
    "account.move": "accounting",
    "hr.expense": "accounting",
    "project.task": "projects",
}

NATIVE_PRIMARY_IMAGES = {
    ("hr.employee", "image_1920"): "hr",
    ("product.template", "image_1920"): "product_master",
    ("res.partner", "image_1920"): "identity",
}

GENERATED_IMAGE_FIELDS = {
    "image_1024",
    "image_128",
    "image_256",
    "image_512",
    "thumbnail",
}

RECOMPUTED_MODELS = {
    "ir.ui.menu",
    "ir.ui.view",
    "onboarding.onboarding.step",
    "payment.method",
    "payment.provider",
    "portal.entry",
    "res.lang",
}


class AttachmentDatabase(SourceDatabase):
    """Read the attachment perimeter through PostgreSQL read-only sessions."""

    def attachment_rows(self) -> list[dict[str, Any]]:
        rows = self.csv(
            r"""
COPY (
    SELECT attachment.id, attachment.name,
           COALESCE(attachment.store_fname, '') AS store_fname,
           COALESCE(attachment.checksum, '') AS checksum,
           COALESCE(attachment.file_size, 0) AS file_size,
           COALESCE(attachment.type, '') AS type,
           COALESCE(attachment.mimetype, '') AS mimetype,
           COALESCE(attachment.res_model, '') AS res_model,
           COALESCE(attachment.res_id, 0) AS res_id,
           COALESCE(attachment.res_field, '') AS res_field,
           COALESCE(attachment.company_id, 0) AS company_id,
           COALESCE(attachment.create_uid, 0) AS create_uid,
           attachment.public,
           attachment.db_datas IS NOT NULL AS has_db_data,
           COALESCE(attachment.url, '') AS url,
           attachment.create_date, attachment.write_date
      FROM ir_attachment attachment
     ORDER BY attachment.id
) TO STDOUT WITH CSV HEADER;
""",
        )
        integer_fields = ("id", "file_size", "res_id", "company_id", "create_uid")
        for row in rows:
            for field in integer_fields:
                row[field] = int(row[field] or 0)
            row["has_db_data"] = row["has_db_data"] == "t"
            row["public"] = row["public"] == "t"
        return rows

    def relationships(self) -> dict[str, Any]:
        document_rows = self.csv(
            r"""
COPY (
    SELECT id, attachment_id, COALESCE(company_id, 0) AS company_id,
           COALESCE(owner_id, 0) AS owner_id,
           COALESCE(res_model, '') AS res_model,
           COALESCE(res_id, 0) AS res_id,
           COALESCE(access_internal, '') AS access_internal,
           active, type
      FROM documents_document
     WHERE attachment_id IS NOT NULL
     ORDER BY id
) TO STDOUT WITH CSV HEADER;
""",
        )
        for row in document_rows:
            for field in ("id", "attachment_id", "company_id", "owner_id", "res_id"):
                row[field] = int(row[field] or 0)
            row["active"] = row["active"] == "t"
        message_rows = self.csv(
            "COPY (SELECT message_id, attachment_id FROM message_attachment_rel "
            "ORDER BY message_id, attachment_id) TO STDOUT WITH CSV HEADER;",
        )
        sign_rows = self.csv(
            "COPY (SELECT id, attachment_id FROM sign_document "
            "ORDER BY id) TO STDOUT WITH CSV HEADER;",
        )
        return {
            "documents": document_rows,
            "message_attachment_ids": {
                int(row["attachment_id"]) for row in message_rows
            },
            "sign_document_attachment_ids": {
                int(row["attachment_id"]) for row in sign_rows
            },
        }


def action(kind: str, scope: str, state: str, reason: str) -> dict[str, str]:
    return {"kind": kind, "scope": scope, "state": state, "reason": reason}


def classify_attachment(
    row: dict[str, Any],
    *,
    document_rows: list[dict[str, Any]],
    message_attachment_ids: set[int],
    sign_document_attachment_ids: set[int],
) -> list[dict[str, str]]:
    """Return every required action for one source identity.

    Ordering is stable and meaningful: operational preservation precedes the
    archive representation, followed by regenerated derivatives.
    """
    result: list[dict[str, str]] = []
    model = row["res_model"]
    field = row["res_field"]
    attachment_id = row["id"]

    if model == "certificate.key":
        return [
            action(
                "revoke_and_reenroll",
                "credential_state",
                "implemented",
                "private key material must not be copied to the rebuilt environment",
            ),
        ]

    if model.startswith("knowledge."):
        return [
            action(
                "discard_demo_knowledge_attachment",
                "knowledge",
                "implemented",
                "approved product decision: source Knowledge content is unused "
                "default/demo data and is not part of Distribution business evidence",
            ),
        ]

    if model.startswith("ai."):
        return [
            action(
                "archive_restricted_business_evidence",
                "documents",
                "implemented",
                "the source binary is retained privately as business evidence while "
                "the experimental AI configuration and index are not copied",
            ),
        ]

    if row["type"] == "url":
        return [
            action(
                "recompute_reference",
                "native_reference",
                "implemented",
                "URL attachments contain no source binary",
            ),
        ]

    if not model and row.get("name") == "res.company.scss":
        return [
            action(
                "recompute_distribution_asset",
                "native_reference",
                "implemented",
                "the company report stylesheet is generated from target configuration",
            ),
        ]

    restored_scope = RESTORED_BUSINESS_MODELS.get(model)
    if restored_scope:
        result.append(
            action(
                "restore_operational_attachment",
                restored_scope,
                "implemented",
                "the owning business restore stage validates the copied bytes by source identity",
            ),
        )

    if (model, field) in NATIVE_PRIMARY_IMAGES:
        scope = NATIVE_PRIMARY_IMAGES[model, field]
        result.append(
            action(
                "restore_native_binary_field",
                scope,
                "implemented",
                "the owning restore stage validates the high-resolution source bytes through the ORM",
            ),
        )
    elif (
        field in GENERATED_IMAGE_FIELDS
        or (model == "ir.attachment" and field == "thumbnail")
        or (model, field) == ("res.company", "logo_web")
    ):
        result.append(
            action(
                "regenerate_derivative",
                "native_reference",
                "implemented",
                "Odoo regenerates this resized image or thumbnail from its authoritative original",
            ),
        )
    elif model in RECOMPUTED_MODELS:
        result.append(
            action(
                "recompute_distribution_asset",
                "native_reference",
                "implemented",
                "the asset is generated from installed modules or target configuration",
            ),
        )

    if document_rows:
        result.append(
            action(
                "archive_document_original",
                "documents",
                "implemented",
                "the Documents archive stage verifies the exact original and preserves every source identity in sealed evidence",
            ),
        )

    if model == "sign.request.item" and field == "signature":
        result.append(
            action(
                "retain_rendered_mark_in_signed_result",
                "signing",
                "implemented",
                "the exact signed PDF preserves the rendered mark; the standalone image is checksum-inventoried but not restored as an impersonation-capable signing asset",
            ),
        )
    elif model == "res.users" and field.startswith("sign_"):
        result.append(
            action(
                "discard_reusable_signing_preference",
                "signing",
                "implemented",
                "reusable signature and initials images are checksum-inventoried but deliberately not copied into the new signing identity",
            ),
        )
    elif model.startswith("sign.") or attachment_id in sign_document_attachment_ids:
        result.append(
            action(
                "archive_signing_evidence",
                "signing",
                "implemented",
                "the external Sign stage preserves the exact business artifact in Paperless or reuses its checksum-identical governed archive root",
            ),
        )

    if model == "spreadsheet.dashboard":
        dashboard_id = row["res_id"]
        if dashboard_id in {1, 2, 7}:
            result.append(
                action(
                    "recompute_distribution_asset",
                    "preferences",
                    "implemented",
                    "the native target provides the corresponding Accounting, "
                    "Invoicing, or Expenses reporting",
                ),
            )
        elif dashboard_id in {3, 8, 9, 10, 11, 12}:
            result.append(
                action(
                    "deliberately_not_copied",
                    "preferences",
                    "implemented",
                    "the locked source row is a packaged Enterprise sample "
                    "dashboard, not USL business data",
                ),
            )
    if attachment_id in message_attachment_ids and not restored_scope:
        result.append(
            action(
                "restore_collaboration_attachment",
                "collaboration",
                "implemented",
                "Collaboration restores the source message relationship and verifies the owning operational or Documents archive bytes",
            ),
        )

    if not result:
        if model == "documents.document" and field == "thumbnail":
            # Defensive fallback; normally handled by GENERATED_IMAGE_FIELDS.
            result.append(
                action(
                    "regenerate_derivative",
                    "native_reference",
                    "implemented",
                    "Documents thumbnail is derived cache data",
                ),
            )
        elif not model:
            result.append(
                action(
                    "archive_unassigned_evidence",
                    "documents",
                    "implemented",
                    "the Documents archive stage preserves unassigned source files in Needs review and verifies their archive bytes",
                ),
            )
        else:
            result.append(
                action(
                    "resolve_downstream_owner",
                    "attachments",
                    "pending",
                    "no implemented migration stage currently owns this operational binary",
                ),
            )

    # The same byte can require several representations, but duplicate action
    # types on one source identity are never meaningful.
    deduplicated = {item["kind"]: item for item in result}
    return [deduplicated[key] for key in sorted(deduplicated)]


def build_ledger(
    package: dict[str, Any],
    database: AttachmentDatabase,
) -> dict[str, Any]:
    database_status = database.assert_source()
    rows = database.attachment_rows()
    relationships = database.relationships()
    integrity_rows = [
        {
            "id": row["id"],
            "store_fname": row["store_fname"],
            "checksum": row["checksum"],
            "file_size": row["file_size"],
            "type": row["type"],
            "mimetype": row["mimetype"],
            "res_model": row["res_model"],
            "res_id": row["res_id"],
            "has_db_data": row["has_db_data"],
            "url": row["url"],
        }
        for row in rows
    ]
    filestore, integrity_errors = verify_filestore(
        Path(package["filestore"]),
        integrity_rows,
    )
    documents_by_attachment: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for document in relationships["documents"]:
        documents_by_attachment[document["attachment_id"]].append(document)

    entries = []
    action_counts: Counter[str] = Counter()
    scope_counts: Counter[str] = Counter()
    pending_counts: Counter[str] = Counter()
    checksum_ids: dict[str, list[int]] = defaultdict(list)
    for row in rows:
        actions = classify_attachment(
            row,
            document_rows=documents_by_attachment[row["id"]],
            message_attachment_ids=relationships["message_attachment_ids"],
            sign_document_attachment_ids=relationships["sign_document_attachment_ids"],
        )
        for item in actions:
            action_counts[item["kind"]] += 1
            scope_counts[item["scope"]] += 1
            if item["state"] != "implemented":
                pending_counts[item["scope"]] += 1
        if row["checksum"]:
            checksum_ids[row["checksum"]].append(row["id"])
        entries.append(
            {
                **row,
                "document_ids": [item["id"] for item in documents_by_attachment[row["id"]]],
                "actions": actions,
            },
        )

    canonical = [
        {
            "id": entry["id"],
            "checksum": entry["checksum"],
            "store_fname": entry["store_fname"],
            "file_size": entry["file_size"],
            "type": entry["type"],
            "mimetype": entry["mimetype"],
            "res_model": entry["res_model"],
            "res_id": entry["res_id"],
            "res_field": entry["res_field"],
            "document_ids": entry["document_ids"],
            "actions": entry["actions"],
        }
        for entry in entries
    ]
    ledger_digest = hashlib.sha256(
        json.dumps(
            canonical,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode(),
    ).hexdigest()
    pending_entries = [
        entry["id"]
        for entry in entries
        if any(item["state"] != "implemented" for item in entry["actions"])
    ]
    unresolved_entries = [
        entry["id"]
        for entry in entries
        if any(item["kind"] == "resolve_downstream_owner" for item in entry["actions"])
    ]
    blocking = {
        "integrity_errors": integrity_errors,
        "unresolved_attachment_ids": unresolved_entries,
        "pending_attachment_ids": pending_entries,
    }
    return {
        "schema": SCHEMA,
        "generated_at": datetime.now(UTC).isoformat(),
        "source": {
            **package,
            "database": database_status["database"],
            "installed_modules": int(database_status["installed_modules"]),
        },
        "summary": {
            "attachment_rows": len(entries),
            "stored_rows": sum(bool(row["store_fname"]) for row in entries),
            "url_rows": sum(row["type"] == "url" for row in entries),
            "unique_stored_checksums": len(checksum_ids),
            "duplicate_checksum_groups": sum(
                len(ids) > 1 for ids in checksum_ids.values()
            ),
            "documents_relationships": len(relationships["documents"]),
            "message_attachment_relationships": len(
                relationships["message_attachment_ids"],
            ),
            "action_counts": dict(sorted(action_counts.items())),
            "scope_action_counts": dict(sorted(scope_counts.items())),
            "pending_scope_counts": dict(sorted(pending_counts.items())),
            "ledger_sha256": ledger_digest,
            "complete": not any(blocking.values()),
        },
        "filestore": filestore,
        "blocking": blocking,
        "entries": entries,
    }


def write_ledger(ledger: dict[str, Any], root: Path) -> tuple[Path, Path]:
    root.mkdir(parents=True, exist_ok=True)
    root.chmod(0o700)
    snapshot = f"source-{ledger['source']['dump_sha256'][:12]}"
    directory = root / snapshot
    directory.mkdir(parents=True, exist_ok=True)
    directory.chmod(0o700)
    json_path = directory / "attachment-disposition-ledger.json"
    csv_path = directory / "attachment-disposition-ledger.csv"
    json_path.write_text(
        json.dumps(ledger, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    json_path.chmod(0o600)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "id", "name", "res_model", "res_id", "res_field", "mimetype",
                "file_size", "checksum", "store_fname", "document_ids", "actions",
            ),
        )
        writer.writeheader()
        for entry in ledger["entries"]:
            writer.writerow(
                {
                    **{field: entry[field] for field in writer.fieldnames[:-2]},
                    "document_ids": ",".join(map(str, entry["document_ids"])),
                    "actions": ";".join(
                        f"{item['kind']}:{item['scope']}:{item['state']}"
                        for item in entry["actions"]
                    ),
                },
            )
    csv_path.chmod(0o600)
    return json_path, csv_path


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("inventory", "gate"))
    parser.add_argument("--source-dir", type=Path, default=DEFAULT_SOURCE_DIR)
    parser.add_argument("--artifacts", type=Path, default=DEFAULT_ARTIFACTS)
    parser.add_argument("--container", default=os.environ.get("MIGRATION_SOURCE_CONTAINER"))
    parser.add_argument(
        "--database",
        default=os.environ.get("MIGRATION_SOURCE_DATABASE", "odoo_online_source_saas_19_3"),
    )
    parser.add_argument("--user", default=os.environ.get("MIGRATION_SOURCE_USER", "odoo"))
    return parser.parse_args(argv)


def print_summary(ledger: dict[str, Any], paths: tuple[Path, Path]) -> None:
    summary = ledger["summary"]
    print(f"Source dump: {ledger['source']['dump_sha256']}")
    print(
        f"Attachments: {summary['attachment_rows']} rows; "
        f"{summary['unique_stored_checksums']} unique stored checksums; "
        f"{summary['documents_relationships']} Documents relationships",
    )
    print(f"Disposition digest: {summary['ledger_sha256']}")
    print(f"Evidence: {paths[0]} and {paths[1]}")
    if summary["complete"]:
        print("PASS: every source attachment is integrity-clean and its actions are implemented.")
        return
    print("BLOCKED: attachment reconstruction is not complete.")
    for key, value in ledger["blocking"].items():
        if value:
            print(f"- {key}: {len(value)}" if isinstance(value, list) else f"- {key}: {value}")
    for scope, count in summary["pending_scope_counts"].items():
        print(f"- pending {scope}: {count} actions")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        package = source_package(args.source_dir)
        ledger = build_ledger(
            package,
            AttachmentDatabase(
                resolve_source_container(args.container),
                args.database,
                args.user,
            ),
        )
        paths = write_ledger(ledger, args.artifacts)
        print_summary(ledger, paths)
        return 1 if args.command == "gate" and not ledger["summary"]["complete"] else 0
    except (AuditError, OSError, ValueError) as error:
        print(f"Attachment ledger failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
