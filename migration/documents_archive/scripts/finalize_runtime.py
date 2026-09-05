"""Finalize the delivered Documents runtime after every restoring stage.

This migration-only gate deliberately runs after all modules and source-backed
records have been finalized.  It reconciles late Odoo attachments with the
already-restored Paperless archive, drains ingestion work, and only then
enables the approved steady-state Documents schedulers.
"""

import hashlib
import json
import os
import time
from collections import Counter, defaultdict
from pathlib import Path

import psycopg2
import psycopg2.extras
from odoo import fields

from migration.documents_archive.provenance import (
    SourceAttachment,
    SourceTimestamps,
    add_timestamps,
    select_source_attachment,
)


def source_text(value):
    if isinstance(value, dict):
        return (
            value.get("fr_FR")
            or value.get("en_US")
            or next((item for item in value.values() if item), "")
        )
    return value or ""


def source_record_key(row):
    if row["res_model"] == "account.move" and row["move_name"]:
        return (
            "account.move",
            row["move_company_id"],
            row["move_name"],
            row["move_ref"] or "",
            str(row["move_date"]),
            row["move_type"],
        )
    if row["res_model"] == "hr.expense" and row["expense_name"]:
        return (
            "hr.expense",
            row["expense_company_id"],
            source_text(row["expense_name"]),
            str(row["expense_date"]),
            round(float(row["expense_total_amount"]), 6),
            row["expense_payment_mode"],
        )
    if row["res_model"] in {"project.project", "project.task"}:
        return (row["res_model"], row["res_id"])
    return None


def source_timestamp_inventory():
    connection = psycopg2.connect(
        host=os.getenv("DOCUMENTS_SOURCE_DB_HOST", "accounting-source-db"),
        port=int(os.getenv("DOCUMENTS_SOURCE_DB_PORT", "5432")),
        user=os.getenv("DOCUMENTS_SOURCE_DB_USER", "odoo"),
        password=(
            os.getenv("DOCUMENTS_SOURCE_DB_PASSWORD")
            or os.getenv("POSTGRES_PASSWORD", "odoo")
        ),
        dbname=os.getenv(
            "DOCUMENTS_SOURCE_DATABASE",
            "odoo_online_source_saas_19_3",
        ),
        cursor_factory=psycopg2.extras.RealDictCursor,
    )
    connection.set_session(readonly=True, autocommit=False)
    with connection, connection.cursor() as cursor:
        cursor.execute("SHOW transaction_read_only")
        if cursor.fetchone()["transaction_read_only"] != "on":
            raise RuntimeError("Documents timestamp source is not read-only.")
        cursor.execute(
            """
            SELECT attachment.id AS source_id, attachment.checksum,
                   attachment.name, COALESCE(attachment.res_model, '') AS res_model,
                   COALESCE(attachment.res_id, 0) AS res_id,
                   attachment.store_fname, attachment.file_size, attachment.mimetype,
                   attachment.create_date AS created_at,
                   COALESCE(attachment.write_date, attachment.create_date) AS modified_at,
                   move.company_id AS move_company_id, move.name AS move_name,
                   move.ref AS move_ref, move.date AS move_date,
                   move.move_type AS move_type,
                   expense.company_id AS expense_company_id,
                   expense.name AS expense_name, expense.date AS expense_date,
                   expense.total_amount AS expense_total_amount,
                   expense.payment_mode AS expense_payment_mode
              FROM ir_attachment attachment
         LEFT JOIN account_move move
                ON attachment.res_model = 'account.move'
               AND move.id = attachment.res_id
         LEFT JOIN hr_expense expense
                ON attachment.res_model = 'hr.expense'
               AND expense.id = attachment.res_id
             WHERE attachment.type = 'binary'
               AND attachment.checksum IS NOT NULL
             ORDER BY attachment.id
            """,
        )
        source_attachment_rows = [dict(row) for row in cursor.fetchall()]
        attachments_by_id = {}
        attachments_by_checksum = defaultdict(list)
        for row in source_attachment_rows:
            source_attachment = SourceAttachment(
                source_id=row["source_id"],
                name=row["name"] or "",
                res_model=row["res_model"],
                file_size=row["file_size"],
                mimetype=row["mimetype"],
                record_key=source_record_key(row),
                timestamps=SourceTimestamps(
                    row["created_at"],
                    row["modified_at"],
                ),
            )
            attachments_by_id[source_attachment.source_id] = source_attachment
            attachments_by_checksum[row["checksum"]].append(source_attachment)
        cursor.execute(
            """
            WITH selected AS (
                SELECT attachment.id AS attachment_id,
                       attachment.store_fname, attachment.checksum,
                       attachment.file_size,
                       document.create_date AS created_at,
                       COALESCE(document.write_date, document.create_date) AS modified_at,
                       document.name AS document_name
                  FROM documents_document document
                  JOIN ir_attachment attachment
                    ON attachment.id = document.attachment_id
                UNION ALL
                SELECT attachment.id, attachment.store_fname,
                       attachment.checksum, attachment.file_size,
                       attachment.create_date,
                       COALESCE(attachment.write_date, attachment.create_date),
                       NULL AS document_name
                  FROM ir_attachment attachment
                 WHERE (
                        COALESCE(attachment.res_model, '') = ''
                        OR attachment.res_model = 'ai.agent.source'
                       )
                   AND attachment.type = 'binary'
                   AND attachment.name != 'res.company.scss'
                   AND NOT EXISTS (
                       SELECT 1 FROM message_attachment_rel relation
                        WHERE relation.attachment_id = attachment.id
                   )
            )
            SELECT * FROM selected ORDER BY attachment_id
            """,
        )
        document_members = [dict(row) for row in cursor.fetchall()]
    connection.close()

    filestore = Path(
        os.getenv(
            "DOCUMENTS_SOURCE_FILESTORE",
            "/mnt/accounting-source/filestore",
        ),
    ).resolve()
    if not filestore.is_dir():
        raise RuntimeError("Frozen source filestore is unavailable.")
    sha256_by_sha1 = {}
    document_timestamps = {}

    def content_sha256(row, *, required):
        source_sha1 = row["checksum"]
        source_sha256 = sha256_by_sha1.get(source_sha1)
        if source_sha256:
            return source_sha256
        if not row.get("store_fname"):
            if required:
                raise RuntimeError(
                    f"Source attachment {row.get('attachment_id') or row.get('source_id')} "
                    "has no filestore identity.",
                )
            return None
        path = (filestore / row["store_fname"]).resolve()
        if filestore not in path.parents or not path.is_file():
            if required:
                raise RuntimeError(
                    f"Source attachment {row.get('attachment_id') or row.get('source_id')} "
                    "has no safe file.",
                )
            return None
        content = path.read_bytes()
        if row.get("file_size") is not None and len(content) != row["file_size"]:
            raise RuntimeError(
                f"Source attachment {row.get('attachment_id') or row.get('source_id')} "
                "size changed.",
            )
        actual_sha1 = hashlib.sha1(content, usedforsecurity=False).hexdigest()
        if actual_sha1 != source_sha1:
            raise RuntimeError(
                f"Source attachment {row.get('attachment_id') or row.get('source_id')} "
                "checksum changed.",
            )
        source_sha256 = hashlib.sha256(content).hexdigest()
        sha256_by_sha1[source_sha1] = source_sha256
        return source_sha256

    for row in document_members:
        source_sha256 = content_sha256(row, required=True)
        add_timestamps(
            document_timestamps,
            source_sha256,
            row["created_at"],
            row["modified_at"],
        )
    document_alias_timestamps = {}
    for row in document_members:
        source_sha256 = content_sha256(row, required=True)
        aggregate = document_timestamps[source_sha256]
        created_second = row["created_at"].replace(microsecond=0)
        for name in {
            source_text(row.get("document_name")),
            source_text(row.get("name")),
        } - {""}:
            add_timestamps(
                document_alias_timestamps,
                (name, created_second),
                aggregate.created_at,
                aggregate.modified_at,
            )
    binary_timestamps = {}
    for row in source_attachment_rows:
        source_sha256 = content_sha256(row, required=False)
        if source_sha256 and row.get("name"):
            add_timestamps(
                binary_timestamps,
                (source_sha256, row["name"]),
                row["created_at"],
                row["modified_at"],
            )
    return (
        attachments_by_id,
        attachments_by_checksum,
        document_timestamps,
        document_alias_timestamps,
        binary_timestamps,
        len(document_members),
    )


def target_record_key(attachment):
    if attachment.res_model not in {
        "account.move",
        "hr.expense",
        "project.project",
        "project.task",
    }:
        return None
    record = env[attachment.res_model].sudo().browse(attachment.res_id).exists()  # noqa: F821
    if not record:
        return None
    if attachment.res_model == "account.move":
        return (
            "account.move",
            record.company_id.id,
            record.name,
            record.ref or "",
            str(record.date),
            record.move_type,
        )
    if attachment.res_model == "hr.expense":
        return (
            "hr.expense",
            record.company_id.id,
            record.name,
            str(record.date),
            round(record.total_amount, 6),
            record.payment_mode,
        )
    return (attachment.res_model, record.id)


Attachment = env["ir.attachment"].sudo()  # noqa: F821 - Odoo shell global
Document = env["usl.document"].sudo()  # noqa: F821
Operation = env["usl.document.operation"].sudo().with_context(  # noqa: F821
    usl_documents_trusted_backfill_access=True,
)

approved_crons = env["ir.cron"].browse(  # noqa: F821
    [
        env.ref("usl_documents.ir_cron_usl_documents_sync").id,  # noqa: F821
        env.ref("usl_documents.ir_cron_usl_documents_poll").id,  # noqa: F821
        env.ref("usl_documents.ir_cron_usl_documents_attachment_queue").id,  # noqa: F821
        env.ref("usl_documents.ir_cron_usl_documents_classification").id,  # noqa: F821
    ],
).sudo()

(
    source_attachments_by_id,
    source_attachments_by_checksum,
    source_document_timestamps,
    source_document_alias_timestamps,
    source_binary_timestamps,
    source_member_count,
) = source_timestamp_inventory()
expected_by_document = {}
matched_source_by_attachment = {}
source_match_occurrences = Counter()

# Paperless roots created by the dedicated Online Documents restoration may
# have no surviving Odoo attachment operation.  Match their immutable file
# digests and restore the source Documents metadata before the final sync.
source_root_matches = 0
for document in Document.search([]):
    checksums = set(document.filtered("checksum").mapped("checksum"))
    checksums.update(document.version_ids.filtered("checksum").mapped("checksum"))
    document_matches = {}
    for checksum in checksums:
        candidate = source_document_timestamps.get(checksum)
        if candidate:
            add_timestamps(
                document_matches,
                "document",
                candidate.created_at,
                candidate.modified_at,
            )
    if not document_matches:
        for checksum in checksums:
            for name in {document.name or "", document.original_filename or ""} - {""}:
                candidate = source_binary_timestamps.get((checksum, name))
                if candidate:
                    add_timestamps(
                        document_matches,
                        "document",
                        candidate.created_at,
                        candidate.modified_at,
                    )
    if not document_matches and document.submitted_at:
        source_name = (document.name or "").removesuffix(
            " — searchable archive copy",
        )
        candidate = source_document_alias_timestamps.get(
            (source_name, document.submitted_at.replace(microsecond=0)),
        )
        if candidate:
            add_timestamps(
                document_matches,
                "document",
                candidate.created_at,
                candidate.modified_at,
            )
    timestamps = document_matches.get("document")
    if timestamps:
        document._merge_original_timestamps(
            timestamps.created_at,
            timestamps.modified_at,
        )
        add_timestamps(
            expected_by_document,
            document.id,
            timestamps.created_at,
            timestamps.modified_at,
        )
        source_root_matches += 1

# Do not trust the resumable backfill cursor here.  Restorers may legitimately
# create attachments after that cursor reached "complete".  Re-applying the
# attachment policy is idempotent because operations are unique per attachment
# version and archived fingerprints are reused rather than re-ingested.
attachment_domain = [("type", "=", "binary"), ("res_id", ">", 0)]
attachments = (
    Attachment.search([*attachment_domain, ("res_field", "=", False)])
    | Attachment.search([*attachment_domain, ("res_field", "!=", False)])
).sorted("id")
eligible_ids = []
excluded = Counter()
for index, attachment in enumerate(attachments, start=1):
    eligible, reason = attachment._usl_documents_archive_eligibility()
    if eligible:
        eligible_ids.append(attachment.id)
        source_attachment = None
        if (
            "rebuild_source_id" in attachment._fields
            and attachment.rebuild_source_model == "ir.attachment"
        ):
            source_attachment = source_attachments_by_id.get(
                attachment.rebuild_source_id,
            )
        if not source_attachment:
            record_key = target_record_key(attachment)
            match_key = (
                attachment.checksum,
                attachment.name or "",
                attachment.res_model or "",
                attachment.file_size,
                attachment.mimetype,
                record_key,
            )
            source_attachment = select_source_attachment(
                source_attachments_by_checksum.get(attachment.checksum, []),
                name=attachment.name or "",
                res_model=attachment.res_model or "",
                file_size=attachment.file_size,
                mimetype=attachment.mimetype,
                record_key=record_key,
                occurrence=source_match_occurrences[match_key],
            )
            if source_attachment:
                source_match_occurrences[match_key] += 1
        queue_attachment = attachment
        if source_attachment:
            source_timestamps = source_attachment.timestamps
            matched_source_by_attachment[attachment.id] = source_attachment
            queue_attachment = attachment.with_context(
                usl_documents_original_created_at=source_timestamps.created_at,
                usl_documents_original_modified_at=source_timestamps.modified_at,
            )
        queue_attachment._queue_usl_documents_archive()
    else:
        excluded[reason or "record_policy"] += 1
    if index % 100 == 0:
        env.cr.commit()  # noqa: F821 - bounded idempotent checkpoint
env.cr.commit()  # noqa: F821

deadline = time.monotonic() + 1800
while True:
    active = Operation.search_count(
        [("state", "in", ("pending", "uploading", "processing"))],
    )
    if not active:
        break
    Operation.cron_process_attachment_queue()
    env.cr.commit()  # noqa: F821 - Paperless effects are already external
    Operation.cron_poll_operations()
    env.cr.commit()  # noqa: F821
    if time.monotonic() >= deadline:
        raise RuntimeError(
            "Documents operations did not drain after the final attachment sweep.",
        )
    time.sleep(0.25)

operations = Operation.search(
    [("source_attachment_id", "in", eligible_ids)],
    order="source_attachment_id, id desc",
)
latest_by_attachment = {}
for operation in operations:
    latest_by_attachment.setdefault(operation.source_attachment_id.id, operation)

unaccounted = sorted(set(eligible_ids) - set(latest_by_attachment))
accepted_trash_conflicts = {
    operation.id
    for operation in latest_by_attachment.values()
    if operation.state == "failed"
    and operation.review_reason == "paperless_trash"
    and operation.document_id.availability_state == "trashed"
}
blocking = [
    operation
    for operation in latest_by_attachment.values()
    if operation.state != "archived" and operation.id not in accepted_trash_conflicts
]
unresolved = Operation.search(
    [
        ("state", "in", ("failed", "duplicate")),
        ("acknowledged", "=", False),
        ("id", "not in", sorted(accepted_trash_conflicts)),
    ],
)
if unaccounted or blocking or unresolved:
    raise RuntimeError(
        "Documents finalization found unresolved attachment operations: "
        + json.dumps(
            {
                "blocking_operation_ids": sorted(
                    {operation.id for operation in blocking} | set(unresolved.ids),
                ),
                "unaccounted_attachment_ids": unaccounted,
            },
            sort_keys=True,
        ),
    )

sync = Document.cron_sync_from_paperless()
if not sync or not sync.get("complete"):
    raise RuntimeError(f"Documents synchronization did not complete: {sync}")
classification = Document.reconcile_linked_classification(limit=0)

# Reconstruction keeps every cron paused.  Enable only the four explicitly
# approved Documents jobs, and only after the deterministic drain passed.
approved_crons.write({"active": True})
env.cr.commit()  # noqa: F821

queue_counts = {
    state: Operation.search_count([("state", "=", state)])
    for state in ("pending", "uploading", "processing")
}
queue_counts["duplicate_unacknowledged"] = Operation.search_count(
    [("state", "=", "duplicate"), ("acknowledged", "=", False)],
)
queue_counts["failed_unacknowledged"] = Operation.search_count(
    [("state", "=", "failed"), ("acknowledged", "=", False)],
)
if any(queue_counts.values()):
    raise RuntimeError(
        "Documents queue changed before the final commit: "
        + json.dumps(queue_counts, sort_keys=True),
    )

timestamp_gaps = []
matched_source_operations = 0
for operation in latest_by_attachment.values():
    source_attachment = matched_source_by_attachment.get(
        operation.source_attachment_id.id,
    )
    if not source_attachment or not operation.document_id:
        continue
    source_timestamps = source_attachment.timestamps
    matched_source_operations += 1
    document = operation.document_id
    add_timestamps(
        expected_by_document,
        document.id,
        source_timestamps.created_at,
        source_timestamps.modified_at,
    )

# Source-backed identities are exact, not approximate lower/upper bounds.  Set
# the aggregate after all checksum reuse and links are known so a repeated run
# cannot drift toward reconstruction timestamps.
for document_id, source_timestamps in expected_by_document.items():
    document = Document.browse(document_id).exists()
    if not document:
        continue
    exact_values = {
        "submitted_at": source_timestamps.created_at,
        "original_created_at": source_timestamps.created_at,
        "original_modified_at": source_timestamps.modified_at,
    }
    document.with_context(usl_documents_cache_write=True).write(exact_values)
    document.version_ids.filtered("is_received_original").sudo().write(
        {
            "submitted_at": source_timestamps.created_at,
            "original_created_at": source_timestamps.created_at,
            "original_modified_at": source_timestamps.modified_at,
        },
    )

for operation in latest_by_attachment.values():
    source_attachment = matched_source_by_attachment.get(
        operation.source_attachment_id.id,
    )
    if not source_attachment or not operation.document_id:
        continue
    source_timestamps = source_attachment.timestamps
    document = operation.document_id
    if (
        not document.original_created_at
        or not document.original_modified_at
        or document.original_created_at
        != expected_by_document[document.id].created_at
        or document.original_modified_at
        != expected_by_document[document.id].modified_at
    ):
        timestamp_gaps.append(operation.id)
if timestamp_gaps:
    raise RuntimeError(
        "Documents source timestamps are incomplete for operations "
        + json.dumps(sorted(timestamp_gaps)),
    )

# Legacy Sign restoration created archive roots directly.  Its operational
# request records retain the exact Online audit timestamps, so use those for an
# adopted pre-fix runtime.  New reconstructions pass these values at upload.
repaired_sign_roots = 0
for document in Document.search(
    [
        "|",
        ("original_created_at", "=", False),
        ("original_modified_at", "=", False),
        ("link_ids.res_model", "=", "sign.oca.request"),
        ("link_ids.active", "=", True),
    ],
):
    request_ids = document.link_ids.filtered(
        lambda link: link.active and link.res_model == "sign.oca.request",
    ).mapped("res_id")
    requests = env["sign.oca.request"].sudo().browse(request_ids).exists()  # noqa: F821
    if not requests:
        continue
    document._merge_original_timestamps(
        min(requests.mapped("create_date")),
        max(requests.mapped("write_date")),
    )
    repaired_sign_roots += 1

# Some migration stages create a new archive artifact from restored business
# data rather than importing an Online file.  Its source timestamp is therefore
# its own deterministic submission time, not the reconstruction-wide clock.
repaired_generated_roots = 0
for document in Document.search(
    [
        "|",
        ("original_created_at", "=", False),
        ("original_modified_at", "=", False),
    ],
):
    linked_models = set(
        document.link_ids.filtered("active").mapped("res_model"),
    )
    if not linked_models.intersection({"b2c.accounting.session", "b2c.order"}):
        continue
    generated_at = document.submitted_at or document.paperless_created
    if not generated_at:
        continue
    document._merge_original_timestamps(generated_at, generated_at)
    document.version_ids.filtered("is_received_original").sudo().write(
        {
            "submitted_at": generated_at,
            "original_created_at": generated_at,
            "original_modified_at": generated_at,
        },
    )
    repaired_generated_roots += 1

missing_original_timestamps = Document.search_count(
    [
        "|",
        ("original_created_at", "=", False),
        ("original_modified_at", "=", False),
    ],
)
if missing_original_timestamps:
    raise RuntimeError(
        f"{missing_original_timestamps} Documents lack original timestamps.",
    )

env.cr.commit()  # noqa: F821 - timestamp parity passed as one durable gate

print(  # noqa: T201 - retained in private reconstruction evidence
    "USL_DOCUMENTS_RUNTIME_FINALIZED="
    + json.dumps(
        {
            "active_approved_jobs": len(approved_crons.filtered("active")),
            "classification": classification,
            "eligible_attachments": len(eligible_ids),
            "excluded": dict(sorted(excluded.items())),
            "matched_source_operations": matched_source_operations,
            "matched_source_roots": source_root_matches,
            "repaired_generated_roots": repaired_generated_roots,
            "repaired_sign_roots": repaired_sign_roots,
            "source_document_members": source_member_count,
            "timestamp_gaps": len(timestamp_gaps),
            "queue": queue_counts,
            "scanned_attachments": len(attachments),
        },
        sort_keys=True,
        default=str,
    ),
)
