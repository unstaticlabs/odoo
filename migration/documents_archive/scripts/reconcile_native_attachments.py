"""Reconcile durable Odoo attachments through the delivered archive bridge.

This script is intentionally executed only by the one-shot migration service.
It leaves its source-comparison evidence outside the product database while
using the same product models and policies as normal post-migration uploads.
"""

import json
import os
import time
from collections import Counter

from odoo import fields

from odoo.addons.usl_documents.models.paperless_client import PaperlessNotFound

Attachment = env["ir.attachment"].sudo()  # noqa: F821 - provided by Odoo shell
Document = env["usl.document"].sudo()  # noqa: F821
Operation = env["usl.document.operation"].sudo().with_context(  # noqa: F821
    usl_documents_trusted_backfill_access=True,
)

# A previous attempt may have committed a Paperless upload before the Odoo
# shell transaction failed. Reconcile the supported remote API first so a
# resumed bridge reuses those roots instead of submitting duplicate OCR work.
migration_client = Document._paperless()
migration_client.token = (
    os.environ.get("DOCUMENTS_PAPERLESS_TOKEN") or migration_client.token
)
archive_sync = Document.sync_from_paperless(full=True, client=migration_client)


def consolidate_expense_tags(client):
    """Replace the migration-era expense tag without losing assignments."""
    Tag = env["usl.paperless.tag"].sudo()  # noqa: F821
    legacy = Tag.search(
        [("name", "=ilike", "Employee expenses"), ("active", "=", True)],
        limit=1,
    )
    if not legacy:
        return {"documents": 0, "mode": "already_canonical"}
    canonical = Tag.search(
        [("name", "=ilike", "Expenses"), ("active", "=", True)],
        limit=1,
    )
    if not canonical:
        refreshed = client.update_metadata(
            "tags",
            legacy.paperless_id,
            {"name": "Expenses"},
        )
        legacy.with_context(usl_documents_cache_write=True).write(
            legacy._cache_values(refreshed),
        )
        return {"documents": legacy.document_count, "mode": "renamed"}

    documents = Document.search([("tag_ids", "in", legacy.ids)], order="id")
    for document in documents:
        desired = (document.tag_ids - legacy) | canonical
        desired_paperless_ids = set(desired.mapped("paperless_id"))
        was_trashed = document.availability_state == "trashed"
        if was_trashed:
            client.restore_trashed_documents([document.paperless_id])
            # Paperless removes Trash rows from the normal document queryset.
            # Read the restored root once before PATCH so the subsequent write
            # never races that visibility transition.
            for attempt in range(5):
                try:
                    current = client.get_document(document.paperless_id)
                    break
                except PaperlessNotFound:
                    if attempt == 4:
                        raise
                    time.sleep(0.25 * (2**attempt))
        else:
            current = client.get_document(document.paperless_id)
        try:
            refreshed = (
                current
                if set(current.get("tags") or []) == desired_paperless_ids
                else client.update_document_metadata(
                    document.paperless_id,
                    {"tags": sorted(desired_paperless_ids)},
                )
            )
        finally:
            if was_trashed:
                client.trash_document(document.paperless_id)
        cache_values = Document._paperless_values(refreshed)
        cache_values.pop("source", None)
        # The remote Trash round-trip is migration-only and must not manufacture
        # operational chatter or change the user's original Trash decision.
        if was_trashed:
            cache_values["availability_state"] = "trashed"
        document.with_context(usl_documents_cache_write=True).write(cache_values)

    remaining = Document.search_count([("tag_ids", "in", legacy.ids)])
    entity_references = env["usl.document.context.tag"].sudo().search_count(  # noqa: F821
        [("tag_id", "=", legacy.id)],
    )
    if remaining or entity_references or legacy.child_ids:
        message = "Legacy expense taxonomy is still referenced after consolidation."
        raise RuntimeError(message)
    client.delete_metadata("tags", legacy.paperless_id)
    legacy.with_context(usl_documents_cache_write=True).unlink()
    return {"documents": len(documents), "mode": "merged"}


taxonomy_consolidation = consolidate_expense_tags(migration_client)
# Taxonomy reconciliation is independently idempotent and may involve many
# remote Paperless updates. Preserve the completed unit before the attachment
# queue starts so an interrupted run never repeats all archive mutations.
env.cr.commit()  # noqa: F821 - provided by Odoo shell

# A stopped migration can leave Odoo operations in ``processing`` after the
# corresponding Paperless task has already reached a terminal state. Settle
# those operations before the attachment scan so the normal queue method can
# turn an honest failure into a fresh, idempotent retry. Queuing first would
# observe ``processing`` and strand the operation until a later run.
resume_deadline = time.monotonic() + 1800
while Operation.search_count([("state", "=", "processing")]):
    Operation.cron_poll_operations()
    env.cr.commit()  # noqa: F821 - bounded resumable poll checkpoint
    if not Operation.search_count([("state", "=", "processing")]):
        break
    if time.monotonic() >= resume_deadline:
        message = "Existing Paperless archive operations did not settle before retry."
        raise RuntimeError(message)
    time.sleep(0.25)

attachment_domain = [("type", "=", "binary"), ("res_id", ">", 0)]
# Odoo deliberately adds ``res_field = False`` to generic ir.attachment
# searches. Name both halves explicitly so the final ledger also classifies
# image/binary-field storage without treating it as a Paperless source file.
attachments = (
    Attachment.search([*attachment_domain, ("res_field", "=", False)])
    | Attachment.search([*attachment_domain, ("res_field", "!=", False)])
).sorted("id")
excluded = Counter()
eligible_ids = []
for index, attachment in enumerate(attachments, start=1):
    eligible, reason = attachment._usl_documents_archive_eligibility()
    if eligible:
        eligible_ids.append(attachment.id)
        attachment._queue_usl_documents_archive()
    else:
        excluded[reason or "record_policy"] += 1
    if index % 100 == 0:
        env.cr.commit()  # noqa: F821 - bounded resumable queue checkpoint
env.cr.commit()  # noqa: F821 - include the final partial queue checkpoint

deadline = time.monotonic() + 1800
while time.monotonic() < deadline:
    due = Operation.search_count(
        [
            ("source_attachment_id", "in", eligible_ids),
            ("state", "=", "pending"),
            "|",
            ("next_attempt_at", "=", False),
            ("next_attempt_at", "<=", fields.Datetime.now()),
        ],
    )
    processing = Operation.search_count(
        [
            ("source_attachment_id", "in", eligible_ids),
            ("state", "=", "processing"),
        ],
    )
    if not due and not processing:
        break
    if due:
        Operation.cron_process_attachment_queue()
    if processing:
        Operation.cron_poll_operations()
    # Operations describe real Paperless effects that cannot share PostgreSQL's
    # transaction. Commit every bounded worker pass so retries resume from the
    # last verified result instead of uploading or relinking the whole archive.
    env.cr.commit()  # noqa: F821 - provided by Odoo shell
    time.sleep(0.25)

operations = Operation.search(
    [("source_attachment_id", "in", eligible_ids)],
    order="source_attachment_id, id desc",
)
latest_by_attachment = {}
for operation in operations:
    latest_by_attachment.setdefault(operation.source_attachment_id.id, operation)

outcomes = Counter(
    operation.state for operation in latest_by_attachment.values()
)
review_outcomes = Counter(
    operation.review_reason
    for operation in latest_by_attachment.values()
    if operation.review_reason
)
unaccounted = sorted(set(eligible_ids) - set(latest_by_attachment))
accepted_trash_conflicts = [
    operation
    for operation in latest_by_attachment.values()
    if operation.state == "failed"
    and operation.review_reason == "paperless_trash"
    and operation.document_id.availability_state == "trashed"
]
accepted_trash_conflict_ids = {operation.id for operation in accepted_trash_conflicts}
blocking_operations = [
    operation
    for operation in latest_by_attachment.values()
    if operation.state != "archived"
    and operation.id not in accepted_trash_conflict_ids
]
blocking_issues = [
    {
        "attachment_id": operation.source_attachment_id.id,
        "attachment_name": operation.source_attachment_id.name,
        "error": operation.error_message,
        "operation_id": operation.id,
        "record_id": operation.source_attachment_id.res_id,
        "record_model": operation.source_attachment_id.res_model,
    }
    for operation in blocking_operations
]
result = {
    "schema": "usl.documents.native-attachment-bridge.v1",
    "archive_sync": archive_sync,
    "taxonomy_consolidation": taxonomy_consolidation,
    "scanned": len(attachments),
    "eligible": len(eligible_ids),
    "outcomes": dict(sorted(outcomes.items())),
    "review_outcomes": dict(sorted(review_outcomes.items())),
    "accepted_trash_conflict_attachment_ids": sorted(
        operation.source_attachment_id.id for operation in accepted_trash_conflicts
    ),
    "blocking_issues": blocking_issues,
    "excluded": dict(sorted(excluded.items())),
    "unaccounted_attachment_ids": unaccounted,
}
print(  # noqa: T201 - parsed into sealed external migration evidence
    "USL_NATIVE_ATTACHMENT_BRIDGE=" + json.dumps(result, sort_keys=True),
)

blocking = Counter(operation.state for operation in blocking_operations)
if blocking_operations or unaccounted:
    raise RuntimeError(
        "Native attachment archival is incomplete: "
        + json.dumps(
            {
                "blocking": dict(sorted(blocking.items())),
                "blocking_operation_ids": sorted(
                    operation.id for operation in blocking_operations
                ),
                "issues": blocking_issues,
                "unaccounted": unaccounted,
            },
            sort_keys=True,
        ),
    )

# Odoo's non-interactive shell does not provide the request transaction that
# ordinary model calls receive. The checkpoints above persist truthful remote
# effects; this final commit seals the complete classification evidence.
env.cr.commit()  # noqa: F821 - provided by Odoo shell
