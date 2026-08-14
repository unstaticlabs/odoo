"""Reconcile durable Odoo attachments through the delivered archive bridge.

This script is intentionally executed only by the one-shot migration service.
It leaves its source-comparison evidence outside the product database while
using the same product models and policies as normal post-migration uploads.
"""

import json
import time
from collections import Counter

from odoo import fields

Attachment = env["ir.attachment"].sudo()  # noqa: F821 - provided by Odoo shell
Operation = env["usl.document.operation"].sudo()  # noqa: F821

attachments = Attachment.search(
    [("type", "=", "binary"), ("res_id", ">", 0)],
    order="id",
)
excluded = Counter()
eligible_ids = []
for attachment in attachments:
    eligible, reason = attachment._usl_documents_archive_eligibility()
    if eligible:
        eligible_ids.append(attachment.id)
        attachment._queue_usl_documents_archive()
    else:
        excluded[reason or "record_policy"] += 1

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
unaccounted = sorted(set(eligible_ids) - set(latest_by_attachment))
result = {
    "schema": "usl.documents.native-attachment-bridge.v1",
    "scanned": len(attachments),
    "eligible": len(eligible_ids),
    "outcomes": dict(sorted(outcomes.items())),
    "excluded": dict(sorted(excluded.items())),
    "unaccounted_attachment_ids": unaccounted,
}
print(  # noqa: T201 - parsed into sealed external migration evidence
    "USL_NATIVE_ATTACHMENT_BRIDGE=" + json.dumps(result, sort_keys=True),
)

blocking = {
    state: count
    for state, count in outcomes.items()
    if state not in {"archived"}
}
if blocking or unaccounted:
    raise RuntimeError(
        "Native attachment archival is incomplete: "
        + json.dumps(
            {"blocking": blocking, "unaccounted": unaccounted},
            sort_keys=True,
        ),
    )
