from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    del version
    env = api.Environment(cr, SUPERUSER_ID, {})
    cron = env.ref(
        "usl_tese_payroll.ir_cron_tese_reconcile_documents",
        raise_if_not_found=False,
    )
    if (
        cron
        and cron.interval_number == 5
        and cron.interval_type == "minutes"
    ):
        cron.write({"interval_number": 12, "interval_type": "hours"})
