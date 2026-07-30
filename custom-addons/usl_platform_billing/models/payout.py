from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import float_compare, float_is_zero


class UslPlatformBillingPayout(models.Model):
    _name = "usl.platform.billing.payout"
    _description = "Content Platform Payout"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "payout_date desc, id desc"
    _check_company_auto = True

    session_id = fields.Many2one(
        "usl.platform.billing.session",
        required=True,
        index=True,
        check_company=True,
        ondelete="cascade",
        tracking=True,
    )
    company_id = fields.Many2one(
        "res.company",
        related="session_id.company_id",
        store=True,
        index=True,
    )
    platform_id = fields.Many2one(
        "usl.platform.billing.platform",
        required=True,
        index=True,
        check_company=True,
        ondelete="restrict",
        tracking=True,
    )
    name = fields.Char(compute="_compute_name", store=True)
    payout_date = fields.Date(required=True, tracking=True)
    platform_reference = fields.Char(required=True, index=True, tracking=True)
    platform_currency_id = fields.Many2one(
        "res.currency",
        required=True,
        ondelete="restrict",
        tracking=True,
    )
    net_platform_amount = fields.Monetary(
        string="Platform Net",
        currency_field="platform_currency_id",
        required=True,
        tracking=True,
    )
    commission_rate_snapshot = fields.Float(
        string="Commission % Snapshot",
        required=True,
        tracking=True,
    )
    gross_platform_amount = fields.Monetary(
        string="Calculated Gross",
        currency_field="platform_currency_id",
        compute="_compute_platform_amounts",
        store=True,
    )
    commission_platform_amount = fields.Monetary(
        string="Calculated Commission",
        currency_field="platform_currency_id",
        compute="_compute_platform_amounts",
        store=True,
    )
    bank_currency_id = fields.Many2one(
        "res.currency",
        related="session_id.bank_currency_id",
        store=True,
    )
    bank_received_amount = fields.Monetary(
        string="Bank Amount",
        currency_field="bank_currency_id",
        tracking=True,
    )
    bank_statement_line_id = fields.Many2one(
        "account.bank.statement.line",
        string="Selected Bank Transaction",
        check_company=True,
        copy=False,
        index=True,
        ondelete="restrict",
        tracking=True,
    )
    bank_journal_id = fields.Many2one(
        "account.journal",
        related="bank_statement_line_id.journal_id",
        store=True,
    )
    bank_date = fields.Date(
        related="bank_statement_line_id.date",
        store=True,
    )
    bank_label = fields.Char(
        related="bank_statement_line_id.payment_ref",
        store=True,
    )
    bank_match_score = fields.Integer(readonly=True, copy=False)
    bank_match_status = fields.Selection(
        [
            ("unmatched", "Unmatched"),
            ("selected", "Selected"),
            ("reconciled", "Reconciled"),
            ("blocked", "Blocked"),
        ],
        required=True,
        default="unmatched",
        copy=False,
        tracking=True,
    )
    bank_amount_difference = fields.Monetary(
        currency_field="bank_currency_id",
        readonly=True,
        copy=False,
    )
    bank_date_difference = fields.Integer(readonly=True, copy=False)
    bank_detection_reason = fields.Text(readonly=True, copy=False)
    customer_invoice_id = fields.Many2one(
        "account.move",
        check_company=True,
        copy=False,
        ondelete="restrict",
        tracking=True,
    )
    vendor_bill_id = fields.Many2one(
        "account.move",
        check_company=True,
        copy=False,
        ondelete="restrict",
        tracking=True,
    )
    compensation_move_id = fields.Many2one(
        "account.move",
        check_company=True,
        copy=False,
        ondelete="restrict",
        tracking=True,
    )
    attachment_ids = fields.Many2many(
        "ir.attachment",
        "usl_platform_billing_payout_attachment_rel",
        "payout_id",
        "attachment_id",
        string="Supporting Documents",
        copy=False,
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("generated", "Generated"),
            ("posted", "Posted"),
            ("paid", "Paid"),
            ("cancelled", "Cancelled"),
        ],
        required=True,
        default="draft",
        copy=False,
        tracking=True,
        index=True,
    )
    validation_status = fields.Selection(
        [
            ("ok", "Ready"),
            ("warning", "Warning"),
            ("error", "Error"),
        ],
        compute="_compute_validation",
        store=True,
    )
    validation_message = fields.Text(compute="_compute_validation", store=True)

    _platform_reference_unique = models.Constraint(
        "UNIQUE(company_id, platform_id, platform_reference)",
        "A platform payout reference must be unique within the company.",
    )
    _bank_statement_line_unique = models.Constraint(
        "UNIQUE(bank_statement_line_id)",
        "A bank transaction can be linked to only one platform payout.",
    )

    @api.depends("platform_id.name", "platform_reference", "payout_date")
    def _compute_name(self):
        for payout in self:
            payout.name = " — ".join(
                str(value)
                for value in (
                    payout.platform_id.name,
                    payout.platform_reference,
                    payout.payout_date,
                )
                if value
            )

    @api.depends(
        "net_platform_amount",
        "commission_rate_snapshot",
        "platform_currency_id",
    )
    def _compute_platform_amounts(self):
        for payout in self:
            rate = payout.commission_rate_snapshot or 0.0
            if not payout.platform_currency_id or not 0.0 < rate < 100.0:
                payout.gross_platform_amount = 0.0
                payout.commission_platform_amount = 0.0
                continue
            gross = payout.net_platform_amount / (1.0 - rate / 100.0)
            payout.gross_platform_amount = payout.platform_currency_id.round(gross)
            payout.commission_platform_amount = payout.platform_currency_id.round(
                payout.gross_platform_amount - payout.net_platform_amount,
            )

    @api.depends(
        "platform_id",
        "platform_currency_id",
        "commission_rate_snapshot",
        "net_platform_amount",
        "payout_date",
        "platform_reference",
        "session_id.company_id",
        "bank_statement_line_id",
        "bank_received_amount",
    )
    def _compute_validation(self):
        for payout in self:
            errors = []
            warnings = []
            if not payout.platform_id:
                errors.append(_("Select a platform."))
            elif payout.platform_id.company_id != payout.company_id:
                errors.append(_("The platform belongs to another company."))
            if not payout.platform_reference:
                errors.append(_("Enter the platform payout reference."))
            if not payout.payout_date:
                errors.append(_("Enter the payout date."))
            if not payout.platform_currency_id:
                errors.append(_("Select the platform currency."))
            elif (
                payout.platform_id
                and payout.platform_currency_id != payout.platform_id.currency_id
            ):
                errors.append(_("The payout currency differs from the platform currency."))
            if not 0.0 < payout.commission_rate_snapshot < 100.0:
                errors.append(_("The commission snapshot must be between 0% and 100%."))
            if payout.net_platform_amount <= 0:
                errors.append(_("The platform net amount must be positive."))
            if payout.bank_statement_line_id:
                if payout.bank_statement_line_id.company_id != payout.company_id:
                    errors.append(_("The bank transaction belongs to another company."))
                if payout.bank_statement_line_id.amount <= 0:
                    errors.append(_("The bank transaction must be incoming."))
            else:
                warnings.append(_("No bank transaction has been selected yet."))
            messages = errors or warnings
            payout.validation_status = (
                "error" if errors else "warning" if warnings else "ok"
            )
            payout.validation_message = "\n".join(messages) if messages else False

    @api.constrains(
        "platform_id",
        "platform_currency_id",
        "commission_rate_snapshot",
        "net_platform_amount",
        "session_id",
        "bank_statement_line_id",
    )
    def _check_business_values(self):
        for payout in self:
            if payout.platform_id.company_id != payout.company_id:
                raise ValidationError(_("The platform and session companies must match."))
            if payout.platform_currency_id != payout.platform_id.currency_id:
                raise ValidationError(
                    _("The payout must use the configured platform currency."),
                )
            if not 0.0 < payout.commission_rate_snapshot < 100.0:
                raise ValidationError(
                    _("The commission snapshot must be between 0% and 100%."),
                )
            if payout.net_platform_amount <= 0:
                raise ValidationError(_("The platform net amount must be positive."))
            if payout.bank_statement_line_id and (
                payout.bank_statement_line_id.company_id != payout.company_id
                or payout.bank_statement_line_id.amount <= 0
            ):
                raise ValidationError(
                    _("Select an incoming bank transaction from the session company."),
                )
            if (
                payout.bank_statement_line_id
                and payout.bank_statement_line_id.currency_id
                != payout.bank_currency_id
            ):
                raise ValidationError(
                    _("The bank transaction must use the session bank currency."),
                )

    @api.onchange("platform_id")
    def _onchange_platform_id(self):
        for payout in self.filtered("platform_id"):
            payout.platform_currency_id = payout.platform_id.currency_id
            payout.commission_rate_snapshot = payout.platform_id.commission_rate

    @api.onchange("bank_statement_line_id")
    def _onchange_bank_statement_line_id(self):
        for payout in self.filtered("bank_statement_line_id"):
            line = payout.bank_statement_line_id
            payout.bank_received_amount = line.amount
            payout.bank_match_status = "selected"

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            platform = self.env["usl.platform.billing.platform"].browse(
                values.get("platform_id"),
            )
            if platform:
                values.setdefault("platform_currency_id", platform.currency_id.id)
                values.setdefault("commission_rate_snapshot", platform.commission_rate)
        return super().create(vals_list)

    def write(self, vals):
        protected = {
            "session_id",
            "platform_id",
            "payout_date",
            "platform_reference",
            "platform_currency_id",
            "net_platform_amount",
            "commission_rate_snapshot",
        }
        if protected & set(vals) and self.filtered(
            lambda payout: payout.state not in {"draft"},
        ):
            raise UserError(_("Generated payouts cannot change their accounting basis."))
        return super().write(vals)

    def unlink(self):
        if self.filtered(lambda payout: payout.state != "draft"):
            raise UserError(_("Only draft payouts can be deleted."))
        if (
            not self.env.su
            and not self.env.user.has_group("account.group_account_manager")
        ):
            raise AccessError(_("Only Accounting administrators can delete payouts."))
        return super().unlink()

    def _open_receivable_line(self):
        self.ensure_one()
        return self.customer_invoice_id.line_ids.filtered(
            lambda line: (
                line.account_id.account_type == "asset_receivable"
                and not line.reconciled
            ),
        )[:1]

    def _bank_line_is_amount_consistent(self):
        self.ensure_one()
        bank_line = self.bank_statement_line_id
        if not bank_line:
            return False
        bank_amount = self.bank_currency_id.round(
            self.bank_received_amount or bank_line.amount,
        )
        return float_compare(
            bank_line.amount,
            bank_amount,
            precision_rounding=self.bank_currency_id.rounding,
        ) == 0

    def _reconcile_bank_transaction(self):
        self.ensure_one()
        invoice = self.customer_invoice_id
        bank_line = self.bank_statement_line_id
        if not invoice or invoice.state != "posted":
            raise UserError(_("The customer invoice must be posted first."))
        if not bank_line:
            raise UserError(_("Select a bank transaction first."))
        if bank_line.is_reconciled:
            self.bank_match_status = "reconciled"
            return
        if bank_line.move_id.state != "posted":
            raise UserError(_("The selected bank transaction is not posted."))
        if bank_line.amount <= 0 or not self._bank_line_is_amount_consistent():
            raise UserError(
                _("The selected bank transaction amount no longer matches the payout."),
            )
        receivable = self._open_receivable_line()
        if not receivable:
            if float_is_zero(
                invoice.amount_residual,
                precision_rounding=invoice.currency_id.rounding,
            ):
                return
            raise UserError(_("No open receivable line remains on the customer invoice."))

        values = {"partner_id": invoice.commercial_partner_id.id}
        transaction_currency = bank_line.currency_id
        if invoice.currency_id != transaction_currency:
            if bank_line.foreign_currency_id not in (
                self.env["res.currency"],
                invoice.currency_id,
            ):
                raise UserError(
                    _("The bank transaction already carries another foreign currency."),
                )
            values.update(
                {
                    "foreign_currency_id": invoice.currency_id.id,
                    "amount_currency": self.net_platform_amount,
                },
            )
        elif bank_line.foreign_currency_id:
            raise UserError(
                _("Clear the unexpected foreign currency before reconciliation."),
            )
        bank_line.write(values)
        bank_line.reconcile_data_info = bank_line._default_reconcile_data()
        bank_line._add_account_move_line(receivable)
        if not bank_line.can_reconcile:
            raise UserError(
                _("OCA reconciliation could not balance this bank transaction."),
            )
        bank_line.reconcile_bank_line()
        if not bank_line.is_reconciled:
            raise UserError(_("The bank transaction remains unreconciled."))
        self.write(
            {
                "bank_match_status": "reconciled",
                "state": "paid",
            },
        )
