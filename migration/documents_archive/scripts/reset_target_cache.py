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

counts = {
    "operations": Operation.search_count([]),
    "links": Link.search_count([]),
    "documents": Document.with_context(active_test=False).search_count([]),
}
Operation.search([]).unlink()
Link.search([]).unlink()
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
