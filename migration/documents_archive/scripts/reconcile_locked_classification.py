"""Apply reviewed company and metadata translations to a finalized target."""

import json
import sys
from pathlib import Path

sys.path.insert(0, "/mnt/documents-archive-migration")

from classification import classify_group  # noqa: E402
from selection import resolve_company_scope  # noqa: E402


evidence_path = Path(
    "/mnt/accounting-private/snapshots/source-0b9916db4807/"
    "evidence/documents-restore-full-all.json",
)
if not evidence_path.is_file():
    raise RuntimeError(f"Locked Documents evidence is missing: {evidence_path}")
evidence = json.loads(evidence_path.read_text(encoding="utf-8"))

companies = {}
for source_id, name in ((1, "Unstatic Labs"), (8, "USL MEDIA")):
    matches = env["res.company"].sudo().search([("name", "=", name)])
    if len(matches) != 1:
        raise RuntimeError(f"Target company {name!r} is missing or ambiguous")
    companies[source_id] = matches

tag_model = env["usl.paperless.tag"].sudo()
type_model = env["usl.paperless.document.type"].sudo()
tags_by_name = {record.name: record for record in tag_model.search([])}
types_by_name = {record.name: record for record in type_model.search([])}
documents = env["usl.document"].sudo()
updated = documents.browse()

for item in evidence["documents"]:
    group = [
        {
            **source,
            "create_date": source.get("create_date") or source.get("created"),
        }
        for source in item["source_truth"]
    ]
    scope = resolve_company_scope(group)
    if scope.get("company_inference") != "locked_source_semantic_ledger":
        continue
    company = companies[scope["company_id"]]
    expected_checksum = item["paperless_original_sha256"]
    document = documents.search(
        [
            ("paperless_id", "=", int(item["paperless_id"])),
            ("availability_state", "!=", "permanently_deleted"),
        ],
        limit=1,
    )
    if not document or expected_checksum not in {
        document.checksum,
        *document.version_ids.mapped("checksum"),
    }:
        raise RuntimeError(
            f"Locked Documents root {item['paperless_id']} is missing or changed",
        )
    classification = classify_group(group)
    missing_tags = sorted(set(classification["tags"]) - set(tags_by_name))
    if missing_tags:
        raise RuntimeError(f"Target Documents tags are missing: {missing_tags}")
    document_type = (
        types_by_name.get(classification["document_type"])
        if classification["document_type"]
        else False
    )
    if classification["document_type"] and not document_type:
        raise RuntimeError(
            f"Target Documents type is missing: {classification['document_type']}",
        )
    document.update_archive_metadata(
        {
            "tag_ids": sorted(
                tags_by_name[name].id for name in classification["tags"]
            ),
            "document_type_id": document_type.id if document_type else False,
        },
    )
    document.with_context(
        usl_documents_cache_write=True,
        usl_documents_policy_write=True,
    ).write(
        {
            "company_id": company.id,
            "accounting_evidence": classification["accounting_evidence"],
            "review_state": (
                "needs_attention"
                if classification["needs_attention"]
                else "reviewed"
            ),
            "last_error": False,
        },
    )
    updated |= document

confidentiality_only = documents.search(
    [
        ("review_state", "=", "needs_attention"),
        ("confidentiality", "in", ("hr", "private")),
        ("last_error", "like", "different archive metadata (confidentiality)"),
        ("link_ids.active", "=", True),
    ],
)
confidentiality_only.with_context(usl_documents_cache_write=True).write(
    {"last_error": False},
)
if updated:
    updated.action_sync_permissions()
classification_result = documents.reconcile_linked_classification(limit=0)
env.cr.commit()

print(
    "DOCUMENTS_LOCKED_CLASSIFICATION_RECONCILED",
    {
        "company_translations": len(updated),
        "confidentiality_repairs": len(confidentiality_only),
        "classification": classification_result,
        "needs_review": documents.search_count(
            [("review_state", "=", "needs_attention")],
        ),
    },
)
