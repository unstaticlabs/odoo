from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Re-read the ledger for payslips left "To reconcile" by a stale snapshot.

    The state and control message used to change only when a payroll button was
    pressed, so a payslip settled in Bank Matching kept reading "To reconcile"
    next to residuals of zero. Only that state is touched: a settled payslip is
    never reopened here, and no journal entry or reconciliation is written.
    """
    del version
    env = api.Environment(cr, SUPERUSER_ID, {})
    payslips = (
        env["usl.tese.payslip"]
        .with_context(mail_notrack=True, tracking_disable=True)
        .search([("state", "=", "to_reconcile")])
    )
    payslips._sync_payment_state()
