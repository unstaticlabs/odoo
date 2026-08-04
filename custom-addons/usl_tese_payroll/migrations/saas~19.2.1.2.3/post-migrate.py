from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    del version
    env = api.Environment(cr, SUPERUSER_ID, {})
    payslips = env["usl.tese.payslip"].search([])
    payslips._compute_payment_summary()
    for payslip in payslips.filtered(
        lambda record: (
            record.state == "to_reconcile"
            and record.payment_status == "paid"
            and record.move_id.state == "posted"
        ),
    ):
        payslip.action_finalize(notify=False)
