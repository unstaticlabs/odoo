from odoo import api, models


class AccountPartialReconcile(models.Model):
    _inherit = "account.partial.reconcile"

    def _tese_payslips(self):
        """Payslips whose payroll or settlement entry these partials touch.

        Always called on a sudo recordset. The payslip is hidden from users
        without the HR role, but reconciling a line is an accounting right and
        must not depend on it: only this internal state sync is elevated, the
        record rule and the ACLs stay as they are.
        """
        return (
            self.debit_move_id | self.credit_move_id
        ).move_id.tese_payslip_id

    @api.model_create_multi
    def create(self, vals_list):
        partials = super().create(vals_list)
        partials.sudo()._tese_payslips()._sync_payment_state()
        return partials

    def unlink(self):
        payslips = self.sudo()._tese_payslips()
        result = super().unlink()
        payslips._sync_payment_state()
        return result
