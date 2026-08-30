"""Finalize the delivered Documents runtime after every restoring stage.

This migration-only gate deliberately runs after all modules and source-backed
records have been finalized.  It reconciles late Odoo attachments with the
already-restored Paperless archive, drains ingestion work, and only then
enables the approved steady-state Documents schedulers.
"""

import json
import time
from collections import Counter


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
        attachment._queue_usl_documents_archive()
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

print(  # noqa: T201 - retained in private reconstruction evidence
    "USL_DOCUMENTS_RUNTIME_FINALIZED="
    + json.dumps(
        {
            "active_approved_jobs": len(approved_crons.filtered("active")),
            "classification": classification,
            "eligible_attachments": len(eligible_ids),
            "excluded": dict(sorted(excluded.items())),
            "queue": queue_counts,
            "scanned_attachments": len(attachments),
        },
        sort_keys=True,
        default=str,
    ),
)
