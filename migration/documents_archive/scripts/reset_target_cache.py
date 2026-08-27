"""Clear the Odoo mirror before an explicit disposable Paperless reset."""

import os


if os.environ.get("DOCUMENTS_CANONICAL_RESET_CONFIRMED") != "1":
    raise RuntimeError("Documents target-cache reset was not explicitly confirmed")
if env.cr.dbname != "odoo_dev":  # noqa: F821
    raise RuntimeError("Documents target-cache reset is reserved for odoo_dev")

Operation = env["usl.document.operation"].sudo()  # noqa: F821
Link = env["usl.document.link"].sudo()  # noqa: F821
Document = env["usl.document"].sudo()  # noqa: F821
Parameter = env["ir.config_parameter"].sudo()  # noqa: F821
Evidence = env.get("b2c.provider.evidence")  # noqa: F821

evidence_links = (
    Evidence.sudo().search([("archived_document_id", "!=", False)])
    if Evidence is not None and "archived_document_id" in Evidence._fields
    else Evidence
)

counts = {
    "operations": Operation.search_count([]),
    "links": Link.search_count([]),
    "documents": Document.with_context(active_test=False).search_count([]),
    "b2c_evidence_links": len(evidence_links) if evidence_links else 0,
}
Operation.search([]).unlink()
Link.search([]).unlink()
if evidence_links:
    # The delivered relationship remains restrictive. Only this explicitly
    # confirmed one-shot reset may detach it before rebuilding the same locked
    # archive; the final B2C pass restores every checksum-backed link.
    evidence_links.with_context(b2c_evidence_import=True).write(
        {"archived_document_id": False},
    )
Document.with_context(active_test=False).search([]).unlink()

Parameter.search(
    [
        (
            "key",
            "in",
            [
                "usl_documents.last_sync",
                "usl_documents.sync_checkpoint",
                "usl_documents.sync_cursor_page",
                "usl_documents.sync_error",
                "usl_documents.sync_mode",
                "usl_documents.sync_modified_after",
                "usl_documents.sync_status",
            ],
        ),
    ],
).unlink()
env.cr.commit()  # noqa: F821
print(
    "DOCUMENTS_TARGET_CACHE_RESET="
    + ",".join(f"{key}:{value}" for key, value in sorted(counts.items())),
    flush=True,
)
