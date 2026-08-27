from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    del version
    env = api.Environment(cr, SUPERUSER_ID, {})
    sync_cron = env.ref(
        "usl_documents.ir_cron_usl_documents_sync",
        raise_if_not_found=False,
    )
    if (
        sync_cron
        and sync_cron.interval_number == 12
        and sync_cron.interval_type == "hours"
    ):
        sync_cron.write({"interval_number": 5, "interval_type": "minutes"})

    classification_cron = env.ref(
        "usl_documents.ir_cron_usl_documents_classification",
        raise_if_not_found=False,
    )
    if (
        classification_cron
        and classification_cron.interval_number == 5
        and classification_cron.interval_type == "minutes"
    ):
        classification_cron.write(
            {"interval_number": 12, "interval_type": "hours"},
        )
