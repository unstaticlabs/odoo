"""Reclassify legacy archive relationships through Odoo's product fields.

Run only through the dedicated migration service.  The emitted before/after
ledger is external recovery evidence; no migration model or provenance field
is introduced into the delivered registry.
"""

# ruff: noqa: F821, T201 - Odoo shell injects ``env`` and captures stdout.

import json
import sys
from collections import Counter

sys.path.insert(0, "/mnt/documents-archive-migration")
from role_backfill import resolve_link_role, resolve_root_role  # noqa: E402

Document = env["usl.document"].sudo()
Link = env["usl.document.link"].sudo()

legacy_links = Link.search(
    [
        "|",
        ("attachment_origin", "=", "migration"),
        ("policy_reason", "in", [
            "legacy_relationship_backfill_pending",
            "manual_documents_link",
        ]),
    ],
    order="document_id, id",
)
documents = legacy_links.mapped("document_id")
changes = []
root_counts = Counter()
link_counts = Counter()

for document in documents:
    links = legacy_links.filtered(lambda item: item.document_id == document)
    context_links = document.link_ids.filtered("active")
    root_policy = resolve_root_role(
        record_models=context_links.mapped("res_model"),
        tags=document.tag_ids.mapped("name"),
        accounting_evidence=bool(document.accounting_evidence),
        confidentiality=document.confidentiality,
        explicit_documents_record=document.source != "paperless",
        existing_role=(
            "evidence"
            if document.intake_role == "evidence"
            or "evidence" in context_links.mapped("policy_role")
            else document.intake_role
        ),
    )
    root_counts[root_policy["document_role"]] += 1
    if document.intake_role != root_policy["document_role"]:
        changes.append(
            {
                "kind": "root",
                "record_id": document.id,
                "before": {"document_role": document.intake_role},
                "after": {"document_role": root_policy["document_role"]},
            },
        )
        document.with_context(usl_documents_policy_write=True).write(
            {"intake_role": root_policy["document_role"]},
        )
    for link in links:
        policy = resolve_link_role(
            res_model=link.res_model,
            root_policy=root_policy,
        )
        before = {
            "archive_mode": link.archive_mode,
            "policy_role": link.policy_role,
            "document_role": link.document_role,
            "attachment_origin": link.attachment_origin,
            "policy_reason": link.policy_reason,
        }
        after = {
            "archive_mode": policy["archive_mode"],
            "policy_role": policy["document_role"],
            # Preserve a prior explicit promotion/demotion.  A legacy link whose
            # current role still equals its policy default has no personal edit.
            "document_role": (
                policy["document_role"]
                if link.document_role == link.policy_role
                else link.document_role
            ),
            "attachment_origin": "migration",
            "policy_reason": policy["policy_reason"],
        }
        link_counts[after["document_role"]] += 1
        if before != after:
            changes.append(
                {
                    "kind": "link",
                    "record_id": link.id,
                    "before": before,
                    "after": after,
                },
            )
            link.with_context(usl_documents_link_policy_write=True).write(after)

env.cr.commit()
result = {
    "schema": "usl-documents-archive-role-backfill-v1",
    "candidate_documents": len(documents),
    "candidate_links": len(legacy_links),
    "changed_records": len(changes),
    "root_role_counts": dict(sorted(root_counts.items())),
    "link_role_counts": dict(sorted(link_counts.items())),
    "changes": changes,
}
print("USL_DOCUMENTS_ROLE_BACKFILL=" + json.dumps(result, sort_keys=True))
