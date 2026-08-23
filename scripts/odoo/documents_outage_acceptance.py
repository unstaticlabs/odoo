"""Assert that an unavailable Paperless service degrades safely inside Odoo."""

from odoo.exceptions import UserError


admin = env.ref("base.user_admin")
documents = env["usl.document"].with_user(admin)
result = documents.workspace_data(query="outage acceptance")
if not result.get("degraded"):
    raise AssertionError("Paperless outage did not produce a degraded workspace state")
if not result.get("error"):
    raise AssertionError("Paperless outage did not provide an actionable error")

bill = env["account.move"].search(
    [("ref", "=", "USL-DOCS-CEO-QA-BILL"), ("move_type", "=", "in_invoice")],
    limit=1,
)
if not bill or not bill.exists():
    raise AssertionError("Odoo business records became unavailable during archive outage")

try:
    documents.sync_from_paperless()
except UserError:
    pass
else:
    raise AssertionError("Synchronization unexpectedly succeeded during outage")

params = env["ir.config_parameter"].sudo()
if params.get_str("usl_documents.sync_status") != "failed":
    raise AssertionError("Interrupted synchronization failure was not persisted")
if not params.get_str("usl_documents.sync_cursor_page"):
    raise AssertionError("Interrupted synchronization did not retain a resume cursor")

env.cr.commit()
print(
    "DOCUMENTS_OUTAGE_ACCEPTANCE_OK",
    {
        "odoo_record_id": bill.id,
        "sync_status": params.get_str("usl_documents.sync_status"),
        "resume_page": params.get_str("usl_documents.sync_cursor_page"),
    },
)
