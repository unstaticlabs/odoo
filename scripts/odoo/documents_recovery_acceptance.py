"""Assert that synchronization resumes safely after a Paperless outage."""


admin = env.ref("base.user_admin")
documents = env["usl.document"].with_user(admin)
before = set(documents.search([]).mapped("paperless_id"))
result = documents.sync_from_paperless()
after = set(documents.search([]).mapped("paperless_id"))
if not result["complete"]:
    raise AssertionError("Synchronization did not finish after Paperless recovery")
if not before.issubset(after):
    raise AssertionError("Recovery lost a stable Paperless identity")
params = env["ir.config_parameter"].sudo()
if params.get_str("usl_documents.sync_status") != "healthy":
    raise AssertionError("Synchronization did not return to healthy state")
if params.get_str("usl_documents.sync_cursor_page"):
    raise AssertionError("Successful recovery left a stale resume cursor")
env.cr.commit()
print(
    "DOCUMENTS_RECOVERY_ACCEPTANCE_OK",
    {
        "documents_before": len(before),
        "documents_after": len(after),
        "synchronized": result["synchronized"],
    },
)
