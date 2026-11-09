from odoo import api, models


class AccountPartialReconcile(models.Model):
    _inherit = "account.partial.reconcile"

    def _tese_payslips(self):
        return (
            self.debit_move_id | self.credit_move_id
        ).move_id.tese_payslip_id

    @api.model_create_multi
    def create(self, vals_list):
        partials = super().create(vals_list)
        partials._tese_payslips()._sync_payment_state()
        return partials

    def unlink(self):
        payslips = self._tese_payslips()
        result = super().unlink()
        payslips._sync_payment_state()
        return result
