from odoo import _, fields, models
from odoo.exceptions import UserError

from .constants import TESE_INTERNAL_WRITE_TOKEN


class AccountMove(models.Model):
    _inherit = "account.move"

    tese_payslip_id = fields.Many2one(
        "usl.tese.payslip",
        string="TESE Payroll",
        check_company=True,
        copy=False,
        ondelete="restrict",
        index=True,
    )
    tese_move_role = fields.Selection(
        [
            ("payroll", "Payroll"),
            ("salary_settlement", "Salary settlement"),
            ("tese_settlement", "TESE settlement"),
        ],
        string="TESE Entry Role",
        copy=False,
        index=True,
    )
    tese_attachment_id = fields.Many2one(
        "ir.attachment",
        string="TESE Payroll PDF",
        copy=False,
        ondelete="restrict",
    )

    _tese_payslip_role_unique = models.Constraint(
        "UNIQUE(tese_payslip_id, tese_move_role)",
        "A TESE payroll record can only have one journal entry for each role.",
    )

    def action_post(self):
        payroll_moves = self.filtered(
            lambda move: (
                move.tese_payslip_id
                and move.tese_move_role == "payroll"
                and move.state == "draft"
            ),
        )
        for move in payroll_moves:
            payslip = move.tese_payslip_id
            payslip._check_workflow_access()
            if not payslip.attachment_id:
                raise UserError(_(
                    "Attach the provider payroll PDF before posting this TESE entry.",
                ))
            if move.tese_attachment_id != payslip.attachment_id:
                move.tese_attachment_id = payslip.attachment_id
        result = super().action_post()
        for move in payroll_moves.filtered(lambda item: item.state == "posted"):
            payslip = move.tese_payslip_id
            payslip.with_context(
                _tese_internal_write=TESE_INTERNAL_WRITE_TOKEN,
            ).write({
                "state": "to_reconcile",
                "move_ref": move.ref,
            })
        return result

    def button_cancel(self):
        protected = self.filtered(
            lambda move: move.tese_payslip_id and move.state == "posted",
        )
        if protected:
            raise UserError(_(
                "Posted TESE payroll and settlement entries are immutable. "
                "Create an explicit reversal instead of cancelling them.",
            ))
        return super().button_cancel()

    def button_draft(self):
        protected = self.filtered(
            lambda move: move.tese_payslip_id and move.state == "posted",
        )
        if protected:
            raise UserError(_(
                "Posted TESE payroll and settlement entries are immutable and "
                "cannot be reset to draft.",
            ))
        return super().button_draft()

    def unlink(self):
        if self.filtered("tese_payslip_id"):
            raise UserError(_(
                "A journal entry linked to TESE payroll history cannot be deleted.",
            ))
        return super().unlink()

    def action_open_tese_payslip(self):
        self.ensure_one()
        if not self.tese_payslip_id:
            raise UserError(_("No TESE payroll record is linked."))
        return {
            "type": "ir.actions.act_window",
            "name": _("TESE Payroll"),
            "res_model": "usl.tese.payslip",
            "res_id": self.tese_payslip_id.id,
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "current",
        }
