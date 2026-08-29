from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import float_compare, format_amount, format_date

PAYOUT_WORKFLOW_DEFAULTS = {
    "state": "draft",
    "customer_invoice_id": False,
    "vendor_bill_id": False,
    "compensation_move_id": False,
    "currency_valuation_method": "reference",
}


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
    company_currency_id = fields.Many2one(
        "res.currency",
        related="company_id.currency_id",
        store=True,
    )
    platform_id = fields.Many2one(
        "usl.platform.billing.platform",
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
    currency_valuation_method = fields.Selection(
        [
            ("reference", "Odoo Reference Rate"),
            ("bank", "Effective Bank Rate"),
        ],
        required=True,
        default="reference",
        copy=False,
        tracking=True,
        help=(
            "Bank-created payouts value their generated documents from the actual "
            "company-currency bank amount. Other payouts use Odoo's reference rate."
        ),
    )
    bank_rate_company_amount = fields.Monetary(
        string="Bank-Rate Company Amount",
        currency_field="company_currency_id",
        compute="_compute_effective_bank_rate",
        store=True,
        copy=False,
        help="Company-currency value supplied by the bank transaction.",
    )
    effective_bank_rate = fields.Float(
        string="Effective Bank Rate",
        digits=(16, 10),
        compute="_compute_effective_bank_rate",
        store=True,
        copy=False,
        help="Company-currency units for one unit of platform currency.",
    )
    effective_bank_rate_label = fields.Char(
        string="Effective Rate",
        compute="_compute_effective_bank_rate_label",
    )
    bank_currency_id = fields.Many2one(
        "res.currency",
        related="session_id.bank_currency_id",
        store=True,
    )
    bank_allocation_ids = fields.One2many(
        "usl.platform.billing.bank.allocation",
        "payout_id",
        string="Bank Allocations",
        copy=False,
    )
    bank_statement_line_ids = fields.Many2many(
        "account.bank.statement.line",
        compute="_compute_bank_summary",
        string="Bank Transactions",
    )
    # Kept as a read-only compatibility summary for existing integrations.
    # It is empty when a payout is settled by more than one transaction.
    bank_statement_line_id = fields.Many2one(
        "account.bank.statement.line",
        compute="_compute_bank_summary",
        string="Single Bank Transaction",
    )
    bank_transaction_preview = fields.Json(
        compute="_compute_bank_transaction_preview",
        string="Bank Transaction Preview",
    )
    bank_received_amount = fields.Monetary(
        string="Allocated Bank Amount",
        currency_field="bank_currency_id",
        compute="_compute_bank_summary",
        copy=False,
    )
    bank_reconciled_amount = fields.Monetary(
        string="Reconciled Bank Amount",
        currency_field="bank_currency_id",
        compute="_compute_bank_summary",
    )
    settled_platform_amount = fields.Monetary(
        string="Settled Payout Amount",
        currency_field="platform_currency_id",
        compute="_compute_bank_summary",
    )
    remaining_platform_amount = fields.Monetary(
        string="Remaining Payout Amount",
        currency_field="platform_currency_id",
        compute="_compute_bank_summary",
    )
    bank_match_score = fields.Integer(
        compute="_compute_bank_summary",
        copy=False,
    )
    bank_match_status = fields.Selection(
        [
            ("unmatched", "Unmatched"),
            ("selected", "Linked"),
            ("partial", "Partially Reconciled"),
            ("reconciled", "Reconciled"),
            ("blocked", "Blocked"),
        ],
        compute="_compute_bank_match_status",
        store=True,
        copy=False,
    )
    bank_amount_difference = fields.Monetary(
        currency_field="bank_currency_id",
        compute="_compute_bank_summary",
        copy=False,
    )
    bank_date_difference = fields.Integer(
        compute="_compute_bank_summary",
        copy=False,
    )
    bank_detection_reason = fields.Text(
        compute="_compute_bank_summary",
        copy=False,
    )
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
        "currency_valuation_method",
        "platform_currency_id",
        "company_currency_id",
        "bank_currency_id",
        "bank_allocation_ids.bank_amount",
        "bank_allocation_ids.payout_amount",
    )
    def _compute_effective_bank_rate(self):
        for payout in self:
            payout.bank_rate_company_amount = 0.0
            payout.effective_bank_rate = 0.0
            if (
                payout.currency_valuation_method != "bank"
                or not payout.platform_currency_id
                or not payout.company_currency_id
                or payout.bank_currency_id != payout.company_currency_id
            ):
                continue
            payout_amount = payout.platform_currency_id.round(
                sum(payout.bank_allocation_ids.mapped("payout_amount")),
            )
            bank_amount = payout.company_currency_id.round(
                sum(payout.bank_allocation_ids.mapped("bank_amount")),
            )
            if payout_amount <= 0.0 or bank_amount <= 0.0:
                continue
            payout.bank_rate_company_amount = bank_amount
            payout.effective_bank_rate = bank_amount / payout_amount

    @api.depends(
        "effective_bank_rate",
        "platform_currency_id.name",
        "company_currency_id.name",
    )
    def _compute_effective_bank_rate_label(self):
        for payout in self:
            payout.effective_bank_rate_label = (
                _(
                    "1 %(platform)s = %(rate).6f %(company)s",
                    platform=payout.platform_currency_id.name,
                    rate=payout.effective_bank_rate,
                    company=payout.company_currency_id.name,
                )
                if payout.effective_bank_rate
                and payout.platform_currency_id
                and payout.company_currency_id
                else False
            )

    def _bank_rate_validation_errors(self):
        self.ensure_one()
        if self.currency_valuation_method != "bank":
            return []
        errors = []
        if self.bank_currency_id != self.company_currency_id:
            errors.append(
                _(
                    "Effective bank-rate valuation requires a bank transaction "
                    "in the company currency.",
                ),
            )
        if not self.bank_allocation_ids:
            errors.append(
                _("Effective bank-rate valuation requires a bank transaction."),
            )
        elif self.platform_currency_id:
            allocated = sum(self.bank_allocation_ids.mapped("payout_amount"))
            if float_compare(
                allocated,
                self.net_platform_amount,
                precision_rounding=self.platform_currency_id.rounding,
            ):
                errors.append(
                    _(
                        "The bank-created payout must be fully allocated before "
                        "its effective rate can be applied.",
                    ),
                )
        if not self.effective_bank_rate:
            errors.append(_("The effective bank rate could not be derived."))
        elif (
            self.platform_currency_id == self.company_currency_id
            and self.company_currency_id.compare_amounts(
                self.bank_rate_company_amount,
                self.net_platform_amount,
            )
        ):
            errors.append(
                _(
                    "A same-currency payout must equal its allocated bank amount.",
                ),
            )
        return list(dict.fromkeys(errors))

    @api.depends(
        "net_platform_amount",
        "bank_allocation_ids",
        "bank_allocation_ids.bank_statement_line_id",
        "bank_allocation_ids.bank_amount",
        "bank_allocation_ids.payout_amount",
        "bank_allocation_ids.state",
        "bank_allocation_ids.score",
        "bank_allocation_ids.amount_difference",
        "bank_allocation_ids.date_difference",
        "bank_allocation_ids.detection_reason",
    )
    def _compute_bank_summary(self):
        for payout in self:
            allocations = payout.bank_allocation_ids
            bank_lines = allocations.bank_statement_line_id
            payout.bank_statement_line_ids = bank_lines
            payout.bank_statement_line_id = (
                bank_lines if len(bank_lines) == 1 else False
            )
            payout.bank_received_amount = sum(allocations.mapped("bank_amount"))
            reconciled = allocations.filtered(
                lambda allocation: allocation.state == "reconciled",
            )
            payout.bank_reconciled_amount = sum(
                reconciled.mapped("bank_amount"),
            )
            payout.settled_platform_amount = sum(
                reconciled.mapped("payout_amount"),
            )
            allocated_platform_amount = sum(allocations.mapped("payout_amount"))
            payout.remaining_platform_amount = max(
                0.0,
                payout.platform_currency_id.round(
                    payout.net_platform_amount - allocated_platform_amount,
                )
                if payout.platform_currency_id
                else 0.0,
            )
            payout.bank_match_score = max(allocations.mapped("score") or [0])
            payout.bank_amount_difference = sum(
                allocations.mapped("amount_difference"),
            )
            payout.bank_date_difference = min(
                allocations.mapped("date_difference") or [0],
            )
            reasons = list(
                dict.fromkeys(
                    allocations.mapped("blocked_reason")
                    + allocations.mapped("detection_reason"),
                ),
            )
            payout.bank_detection_reason = "\n".join(filter(None, reasons)) or False

    @api.depends(
        "bank_allocation_ids.bank_statement_line_id",
        "bank_allocation_ids.bank_statement_line_id.date",
        "bank_allocation_ids.bank_statement_line_id.journal_id",
        "bank_allocation_ids.bank_statement_line_id.payment_ref",
        "bank_allocation_ids.bank_statement_line_id.name",
        "bank_allocation_ids.bank_statement_line_id.partner_id",
        "bank_allocation_ids.bank_statement_line_id.partner_name",
        "bank_allocation_ids.bank_statement_line_id.amount",
        "bank_allocation_ids.bank_statement_line_id.currency_id",
        "bank_allocation_ids.bank_statement_line_id.is_reconciled",
    )
    def _compute_bank_transaction_preview(self):
        for payout in self:
            bank_lines = payout.bank_allocation_ids.bank_statement_line_id.sorted(
                key=lambda line: (line.date, line.id),
                reverse=True,
            )
            payout.bank_transaction_preview = [
                {
                    "id": bank_line.id,
                    "display_name": bank_line.display_name,
                    "date": (
                        format_date(self.env, bank_line.date)
                        if bank_line.date
                        else False
                    ),
                    "journal": bank_line.journal_id.display_name,
                    "label": bank_line.payment_ref or bank_line.name,
                    "partner": (
                        bank_line.partner_id.display_name
                        or bank_line.partner_name
                        or False
                    ),
                    "amount": format_amount(
                        self.env,
                        bank_line.amount,
                        bank_line.currency_id,
                    ),
                    "reconciled": bank_line.is_reconciled,
                }
                for bank_line in bank_lines
            ]

    @api.depends(
        "net_platform_amount",
        "platform_currency_id",
        "bank_allocation_ids",
        "bank_allocation_ids.state",
        "bank_allocation_ids.payout_amount",
    )
    def _compute_bank_match_status(self):
        for payout in self:
            allocations = payout.bank_allocation_ids
            reconciled = allocations.filtered(
                lambda allocation: allocation.state == "reconciled",
            )
            settled_amount = sum(reconciled.mapped("payout_amount"))
            if not allocations:
                payout.bank_match_status = "unmatched"
            elif allocations.filtered(lambda allocation: allocation.state == "blocked"):
                payout.bank_match_status = "blocked"
            elif (
                payout.platform_currency_id
                and payout.platform_currency_id.compare_amounts(
                    settled_amount,
                    payout.net_platform_amount,
                )
                >= 0
                and len(reconciled) == len(allocations)
            ):
                payout.bank_match_status = "reconciled"
            elif reconciled:
                payout.bank_match_status = "partial"
            else:
                payout.bank_match_status = "selected"

    @api.depends(
        "platform_id",
        "platform_currency_id",
        "commission_rate_snapshot",
        "net_platform_amount",
        "payout_date",
        "platform_reference",
        "session_id.company_id",
        "bank_allocation_ids",
        "bank_allocation_ids.bank_amount",
        "bank_allocation_ids.state",
        "bank_allocation_ids.payout_amount",
        "currency_valuation_method",
        "bank_currency_id",
        "company_currency_id",
        "effective_bank_rate",
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
            errors.extend(payout._bank_rate_validation_errors())
            if payout.bank_allocation_ids.filtered(
                lambda allocation: allocation.payout_amount <= 0,
            ):
                errors.append(
                    _(
                        "Complete the original payout amount for the imported bank transaction.",
                    ),
                )
            elif not payout.bank_allocation_ids:
                warnings.append(_("No bank transaction has been selected yet."))
            elif payout.remaining_platform_amount:
                warnings.append(
                    _(
                        "%s remains to be linked to a bank transaction.",
                        payout.remaining_platform_amount,
                    ),
                )
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
    )
    def _check_business_values(self):
        for payout in self:
            if payout.platform_id and payout.platform_id.company_id != payout.company_id:
                raise ValidationError(_("The platform and session companies must match."))
            if (
                payout.platform_id
                and payout.platform_currency_id
                and payout.platform_currency_id != payout.platform_id.currency_id
            ):
                raise ValidationError(
                    _("The payout must use the configured platform currency."),
                )
            if payout.net_platform_amount < 0:
                raise ValidationError(_("The platform net amount cannot be negative."))
            if payout.commission_rate_snapshot and not (
                0.0 < payout.commission_rate_snapshot < 100.0
            ):
                raise ValidationError(
                    _("The commission snapshot must be between 0% and 100%."),
                )
            complete = bool(
                payout.platform_id
                and payout.platform_currency_id
                and 0.0 < payout.commission_rate_snapshot < 100.0
                and payout.net_platform_amount > 0,
            )
            if payout.state != "draft" and not complete:
                raise ValidationError(
                    _("Complete the payout accounting details before continuing."),
                )

    @api.onchange("platform_id")
    def _onchange_platform_id(self):
        for payout in self.filtered("platform_id"):
            payout.platform_currency_id = payout.platform_id.currency_id
            payout.commission_rate_snapshot = payout.platform_id.commission_rate

    @api.model_create_multi
    def create(self, vals_list):
        normalized_values = []
        for incoming_values in vals_list:
            values = dict(incoming_values)
            if not self.env.su:
                for field_name, default_value in PAYOUT_WORKFLOW_DEFAULTS.items():
                    if field_name not in values:
                        continue
                    submitted_value = values[field_name]
                    is_default = (
                        submitted_value == default_value
                        if default_value
                        else not submitted_value
                    )
                    if not is_default:
                        raise AccessError(
                            _("Workflow fields can only be changed by app actions."),
                        )
                    values.pop(field_name)
            platform = self.env["usl.platform.billing.platform"].browse(
                values.get("platform_id"),
            )
            if platform:
                values.setdefault("platform_currency_id", platform.currency_id.id)
                values.setdefault("commission_rate_snapshot", platform.commission_rate)
            normalized_values.append(values)
        payouts = super().create(normalized_values)
        payouts._sync_pending_bank_allocations()
        return payouts

    def _write_accounting_defaults(self, values):
        if "platform_id" not in values or not values["platform_id"]:
            return values
        platform = self.env["usl.platform.billing.platform"].browse(
            values["platform_id"],
        )
        values = dict(values)
        values.setdefault("platform_currency_id", platform.currency_id.id)
        values.setdefault("commission_rate_snapshot", platform.commission_rate)
        return values

    def _sync_pending_bank_allocations(self):
        for payout in self.filtered(
            lambda item: (
                item.state == "draft"
                and item.platform_currency_id
                and item.net_platform_amount > 0
            ),
        ):
            pending = payout.bank_allocation_ids.filtered(
                lambda allocation: allocation.payout_amount <= 0,
            )
            if len(pending) == 1:
                pending._action_write(
                    {"payout_amount": payout.net_platform_amount},
                )

    def _strip_unchanged_workflow_values(self, values):
        values = dict(values)
        if self.env.su:
            return values
        for field_name in PAYOUT_WORKFLOW_DEFAULTS.keys() & values.keys():
            field = self._fields[field_name]
            for payout in self:
                payout[field_name]
                submitted = field.convert_to_cache(values[field_name], payout)
                current = payout._cache[field_name]
                if submitted != current and (submitted or current):
                    raise AccessError(
                        _("Workflow fields can only be changed by app actions."),
                    )
            values.pop(field_name)
        return values

    def write(self, vals):
        vals = self._strip_unchanged_workflow_values(vals)
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
        if len(self) == 1:
            vals = self._write_accounting_defaults(vals)
        result = super().write(vals)
        if protected & set(vals):
            self._sync_pending_bank_allocations()
        return result

    def _workflow_write(self, values):
        return super().write(values)

    def unlink(self):
        if self.filtered(lambda payout: payout.state not in {"draft", "cancelled"}):
            raise UserError(_("Only draft or cancelled payouts can be deleted."))
        if (
            not self.env.su
            and not self.env.user.has_group(
                "usl_platform_billing.group_platform_billing_manager",
            )
        ):
            raise AccessError(
                _("Only Platform Billing administrators can delete payouts."),
            )
        return super().unlink()
