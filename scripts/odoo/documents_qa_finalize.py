import time


Document = env["usl.document"].with_user(env.ref("base.user_root"))
Operation = env["usl.document.operation"].with_user(env.ref("base.user_root"))
crons = env["ir.cron"].browse(
    [
        env.ref("usl_documents.ir_cron_usl_documents_sync").id,
        env.ref("usl_documents.ir_cron_usl_documents_poll").id,
        env.ref("usl_documents.ir_cron_usl_documents_attachment_queue").id,
        env.ref("usl_documents.ir_cron_usl_documents_classification").id,
    ],
)
crons.sudo().write({"active": True})
env.cr.commit()

for _attempt in range(600):
    Operation.cron_process_attachment_queue()
    env.cr.commit()
    Operation.cron_poll_operations()
    env.cr.commit()
    pending = Operation.sudo().search_count(
        [("state", "in", ("pending", "uploading", "processing"))],
    )
    backfill_complete = (
        env["ir.config_parameter"].sudo().get_str(
            "usl_documents.attachment_backfill_state",
            "pending",
        )
        == "complete"
    )
    if not pending and backfill_complete:
        break
    time.sleep(1)
else:
    raise RuntimeError("Documents attachment queue did not drain during QA deployment")

sync = Document.cron_sync_from_paperless()
if not sync or not sync.get("complete"):
    raise RuntimeError(f"Documents synchronization did not complete: {sync}")
classification = Document.reconcile_linked_classification(limit=0)
env.cr.commit()

print(
    "DOCUMENTS_QA_FINALIZED",
    {
        "active_jobs": len(crons.filtered("active")),
        "classification": classification,
        "needs_review": Document.sudo().search_count(
            [("review_state", "=", "needs_attention")],
        ),
        "pending_operations": Operation.sudo().search_count(
            [("state", "in", ("pending", "uploading", "processing"))],
        ),
    },
)
