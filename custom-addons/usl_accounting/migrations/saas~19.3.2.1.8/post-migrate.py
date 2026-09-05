from odoo import SUPERUSER_ID, _, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    attention_files = env["account.bank.ingestion.file"].sudo().search(
        [("processing_state", "=", "attention")],
    )
    for source_file in attention_files:
        open_issue = source_file.exception_ids.filtered(
            lambda item: item.state == "open",
        )[:1]
        completed = not open_issue and source_file.statement_id
        detail = source_file.processing_detail or (
            _("Retained file processing was completed by an accounting decision.")
            if completed
            else open_issue.detail or _("Review the retained file and retry.")
        )
        source_file.write(
            {
                "processing_state": "processed" if completed else "failed",
                "processing_detail": detail,
            },
        )
    ingestions = env["account.bank.ingestion"].sudo().search(
        [
            ("state", "in", ("done", "attention", "failed")),
            ("file_ids.processing_state", "=", "pending"),
        ],
    )
    ingestions._finalize_retained_files()
    ingestions.filtered(
        lambda ingestion: (
            ingestion.state == "done"
            and ingestion.exception_ids.filtered(lambda item: item.state == "open")
        ),
    ).write({"state": "attention"})
