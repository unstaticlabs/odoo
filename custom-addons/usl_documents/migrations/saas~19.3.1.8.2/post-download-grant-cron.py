from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    del version
    env = api.Environment(cr, SUPERUSER_ID, {})
    cron = env.ref(
        "usl_documents.ir_cron_usl_document_download_grants_cleanup",
        raise_if_not_found=False,
    )
    if not cron:
        return

    expected_model = "usl.document.download.grant"
    if cron.model_id.model != expected_model:
        raise RuntimeError(
            "The Documents download-grant cleanup cron targets an unexpected model: "
            f"{cron.model_id.model!r}.",
        )

    expected_code = "model._cron_cleanup_download_grants()"
    if cron.code != expected_code:
        cron.write({"code": expected_code})

    # Prove the corrected entry point before clearing the historical failure.
    env[expected_model]._cron_cleanup_download_grants()
    cron.write({"failure_count": 0, "first_failure_date": False})
