from odoo import _, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.addons.usl_accounting.models.bank_statement_ingestion import _month_end

from odoo.addons.usl_accounting.models.bank_statement_review import (
    is_accounting_operator,
)


class BankIngestionPeriod(models.TransientModel):
    _name = "account.bank.ingestion.period"
    _description = "Correct Bank Export Period"

    ingestion_id = fields.Many2one(
        "account.bank.ingestion", required=True, readonly=True
    )
    period_start = fields.Date(required=True)
    period_end = fields.Date(required=True)
    reason = fields.Text(required=True)

    def action_apply(self):
        self.ensure_one()
        if not is_accounting_operator(self.env.user):
            raise AccessError(_("Only an accountant can correct a bank export period."))
        ingestion = self.ingestion_id
        ingestion.check_access("read")
        if ingestion.company_id not in self.env.companies:
            raise AccessError(
                _("Select the export's company before correcting its period.")
            )
        self.env.cr.execute(
            "SELECT id FROM account_bank_ingestion WHERE id = %s FOR UPDATE",
            [ingestion.id],
        )
        ingestion.invalidate_recordset()
        if ingestion.state not in ("attention", "failed"):
            raise UserError(
                _("This export no longer needs correction. Refresh the record.")
            )
        if not self.reason or not self.reason.strip():
            raise UserError(_("Explain how you verified the statement period."))
        if (
            not self.period_start
            or not self.period_end
            or self.period_start > self.period_end
        ):
            raise UserError(_("Enter a valid start and end date."))
        if self.period_start.day != 1 or self.period_end != _month_end(self.period_start):
            raise UserError(_("Enter the first and last day of a single statement month."))
        if self.period_start < ingestion.sudo().config_id.automatic_start_date.replace(day=1):
            raise UserError(_("This period predates the configured ingestion cut-over."))
        period = (self.period_start, self.period_end)
        statements = ingestion.sudo().file_ids.statement_id
        verified_periods = ingestion.sudo()._verified_file_periods()
        if any(item != period for item in verified_periods) or any(
            (statement.period_start, statement.period_end) != period
            for statement in statements
        ):
            raise UserError(
                _(
                    "These dates conflict with the retained bank statement. Check the source files."
                )
            )
        old_start, old_end = ingestion.period_start, ingestion.period_end
        ingestion.sudo().write(
            {"period_start": self.period_start, "period_end": self.period_end}
        )
        ingestion.sudo().message_post(
            body=_(
                "Statement period corrected from %(old_start)s–%(old_end)s to %(start)s–%(end)s. Reason: %(reason)s",
                old_start=old_start or "—",
                old_end=old_end or "—",
                start=self.period_start,
                end=self.period_end,
                reason=self.reason.strip(),
            ),
            author_id=self.env.user.partner_id.id,
        )
        # Reuse retained PDFs only: do not download email links or import transactions.
        for source in ingestion.sudo().file_ids.filtered(
            lambda item: item.classification == "pdf"
        ):
            source._associate_pdf()
        ingestion.sudo()._refresh_processing_state()
        return {"type": "ir.actions.act_window_close"}
