import hashlib
import json

from odoo import _, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import BinaryBytes

from odoo.addons.usl_accounting.models.bank_statement_review import (
    is_accounting_operator,
)

MAX_RECOVERED_FILE_BYTES = 50 * 1024 * 1024


class AccountBankIngestionUpload(models.TransientModel):
    _name = "account.bank.ingestion.upload"
    _description = "Add Recovered Bank Export"

    ingestion_id = fields.Many2one(
        "account.bank.ingestion",
        required=True,
        readonly=True,
    )
    source_file = fields.Binary(required=True)
    source_filename = fields.Char(required=True)

    def action_add_file(self):
        self.ensure_one()
        if not is_accounting_operator(self.env.user):
            raise AccessError(_("Only an accountant can add a recovered bank export."))
        self.ingestion_id.check_access("read")
        content = bytes(self.source_file.content or b"")
        if not content:
            raise UserError(_("Choose a bank export file to retain."))
        if len(content) > MAX_RECOVERED_FILE_BYTES:
            raise UserError(_("The recovered bank export exceeds the 50 MiB limit."))
        attachment = (
            self.env["ir.attachment"]
            .sudo()
            .create(
                {
                    "name": self.source_filename,
                    "raw": BinaryBytes(content),
                    "res_model": self.ingestion_id._name,
                    "res_id": self.ingestion_id.id,
                    "company_id": self.ingestion_id.company_id.id,
                },
            )
        )
        source_file = (
            self.env["account.bank.ingestion.file"]
            .sudo()
            ._from_attachment(
                self.ingestion_id.sudo(),
                attachment,
                recovered_upload=True,
            )
        )
        self.ingestion_id.sudo().write({"state": "received", "last_error": False})
        self.ingestion_id.message_post(
            body=_(
                "Recovered bank export retained: %(name)s",
                name=source_file.filename,
            ),
            attachment_ids=[attachment.id],
        )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "message": _(
                    "The recovered export was retained. Process the source again.",
                ),
                "type": "success",
                "sticky": False,
                "next": {"type": "ir.actions.act_window_close"},
            },
        }


class AccountBankStatementConfirm(models.TransientModel):
    _name = "account.bank.statement.confirm"
    _description = "Confirm Bank-Reported Balances"

    statement_id = fields.Many2one(
        "account.bank.statement",
        required=True,
        readonly=True,
    )
    currency_id = fields.Many2one(related="statement_id.currency_id")
    period_start = fields.Date(related="statement_id.period_start", readonly=True)
    period_end = fields.Date(related="statement_id.period_end", readonly=True)
    balance_start = fields.Monetary(required=True)
    balance_end_real = fields.Monetary(required=True)
    movement_total = fields.Monetary(
        related="statement_id.movement_total",
        readonly=True,
    )
    resulting_difference = fields.Monetary(compute="_compute_resulting_difference")

    def _compute_resulting_difference(self):
        for wizard in self:
            wizard.resulting_difference = wizard.balance_end_real - (
                wizard.balance_start + wizard.movement_total
            )

    def default_get(self, field_names):
        values = super().default_get(field_names)
        statement = self.env["account.bank.statement"].browse(
            self.env.context.get("default_statement_id"),
        )
        if statement:
            values.update(
                {
                    "balance_start": statement.balance_start,
                    "balance_end_real": statement.balance_end_real,
                },
            )
        return values

    def action_confirm(self):
        self.ensure_one()
        if not is_accounting_operator(self.env.user):
            raise AccessError(_("Only an accountant can confirm bank balances."))
        if self.statement_id.certification_state == "certified":
            raise UserError(
                _("Reopen the certified statement before changing its balances."),
            )
        self.statement_id.sudo().with_context(bank_review_internal=True).write(
            {
                "balance_start": self.balance_start,
                "balance_end_real": self.balance_end_real,
                "balances_confirmed": True,
                "balances_confirmed_by_id": self.env.user.id,
                "balances_confirmed_at": fields.Datetime.now(),
            },
        )
        self.statement_id.message_post(
            body=_("Bank-reported opening and closing balances confirmed."),
        )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "message": (
                    _("Balances saved. The statement agrees with the bank.")
                    if self.statement_id.currency_id.compare_amounts(
                        self.resulting_difference,
                        0,
                    )
                    == 0
                    else _(
                        "Balances saved. Resolve the displayed difference before certification.",
                    )
                ),
                "type": "success"
                if self.statement_id.currency_id.compare_amounts(
                    self.resulting_difference,
                    0,
                )
                == 0
                else "warning",
                "sticky": False,
                "next": {"type": "ir.actions.act_window_close"},
            },
        }


class AccountBankStatementReopen(models.TransientModel):
    _name = "account.bank.statement.reopen"
    _description = "Reopen Certified Bank Statement"

    statement_id = fields.Many2one(
        "account.bank.statement",
        required=True,
        readonly=True,
    )
    reason = fields.Text(required=True)

    def action_reopen(self):
        self.ensure_one()
        if not self.env.user.has_group("account.group_account_manager"):
            raise AccessError(
                _("Only an Accounting Manager can reopen a certified statement."),
            )
        statement = self.statement_id
        if statement.certification_state != "certified":
            raise UserError(_("Only a certified statement can be reopened."))
        if not (self.reason or "").strip():
            raise UserError(_("Record why this certified statement is being reopened."))
        self.env.cr.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            [f"account.bank.statement.reopen:{statement.id}"],
        )
        identities = sorted(
            line.provider_transaction_id or line.unique_import_id or f"odoo:{line.id}"
            for line in statement.line_ids.filtered(lambda line: line.state == "posted")
        )
        digest = hashlib.sha256(
            json.dumps(identities, ensure_ascii=True, separators=(",", ":")).encode(),
        ).hexdigest()
        self.env["account.bank.statement.certification"].sudo().create(
            {
                "statement_id": statement.id,
                "company_id": statement.company_id.id,
                "event_type": "reopen",
                "user_id": self.env.user.id,
                "event_at": fields.Datetime.now(),
                "reason": self.reason.strip(),
                "period_start": statement.period_start,
                "period_end": statement.period_end,
                "balance_start": statement.balance_start,
                "movement_total": statement.movement_total,
                "balance_end_real": statement.balance_end_real,
                "transaction_count": len(identities),
                "transaction_identity_digest": digest,
                **statement._bank_evidence_snapshot_values(),
            },
        )
        statement.sudo().with_context(bank_review_internal=True).write(
            {
                "certification_state": "reopened",
                "certified_by_id": False,
                "certified_at": False,
            },
        )
        statement.message_post(
            body=_(
                "Certified bank statement reopened. Reason: %(reason)s",
                reason=self.reason.strip(),
            ),
        )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "message": _("Bank statement reopened for correction."),
                "type": "warning",
                "sticky": False,
                "next": {"type": "ir.actions.act_window_close"},
            },
        }
