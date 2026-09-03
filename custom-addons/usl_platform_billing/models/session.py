from collections import defaultdict

from babel.dates import format_date as babel_format_date
from dateutil.relativedelta import relativedelta

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

SESSION_WORKFLOW_DEFAULTS = {
    "state": "draft",
    "generated_at": False,
    "generated_by_id": False,
}
FRENCH_MONTH_NAMES = {
    "Janvier",
    "Février",
    "Mars",
    "Avril",
    "Mai",
    "Juin",
    "Juillet",
    "Août",
    "Septembre",
    "Octobre",
    "Novembre",
    "Décembre",
}


class UslPlatformBillingSession(models.Model):
    _name = "usl.platform.billing.session"
    _description = "Content Platform Billing Session"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "period_month desc, id desc"
    _check_company_auto = True

    name = fields.Char(required=True, default=lambda self: self._default_name(), tracking=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
        tracking=True,
    )
    period_month = fields.Date(
        required=True,
        default=lambda self: fields.Date.context_today(self).replace(day=1),
        tracking=True,
    )
    invoice_date = fields.Date(required=True, default=fields.Date.context_today, tracking=True)
    due_date = fields.Date(tracking=True)
    bank_currency_id = fields.Many2one(
        "res.currency",
        required=True,
        default=lambda self: self.env.company.currency_id,
        ondelete="restrict",
        tracking=True,
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("ready", "Ready"),
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
    payout_ids = fields.One2many(
        "usl.platform.billing.payout",
        "session_id",
        string="Payouts",
        copy=False,
    )
    generated_move_ids = fields.One2many(
        "account.move",
        "platform_billing_session_id",
        string="Generated Journal Entries",
        copy=False,
    )
    customer_invoice_ids = fields.Many2many(
        "account.move",
        compute="_compute_document_links",
        string="Customer Invoices",
    )
    vendor_bill_ids = fields.Many2many(
        "account.move",
        compute="_compute_document_links",
        string="Commission Bills",
    )
    compensation_move_ids = fields.Many2many(
        "account.move",
        compute="_compute_document_links",
        string="Compensation Entries",
    )
    payout_count = fields.Integer(compute="_compute_summary")
    generated_move_count = fields.Integer(compute="_compute_summary")
    bank_transaction_count = fields.Integer(compute="_compute_summary")
    total_bank_received = fields.Monetary(
        string="Bank Total",
        currency_field="bank_currency_id",
        compute="_compute_summary",
    )
    currency_summary = fields.Text(compute="_compute_summary")
    next_action = fields.Char(compute="_compute_guidance")
    guidance = fields.Text(compute="_compute_guidance")
    generated_at = fields.Datetime(readonly=True, copy=False)
    generated_by_id = fields.Many2one("res.users", readonly=True, copy=False)
    missing_active_platform_ids = fields.Many2many(
        "usl.platform.billing.platform",
        "usl_pb_session_missing_platform_rel",
        "session_id",
        "platform_id",
        compute="_compute_platform_coverage",
        string="Missing Active Platforms",
    )
    platform_coverage_warning = fields.Text(
        compute="_compute_platform_coverage",
    )

    _company_period_name_unique = models.Constraint(
        "UNIQUE(company_id, period_month, name)",
        "A billing session name must be unique for the company and month.",
    )

    @api.model
    def _default_name(self):
        today = fields.Date.context_today(self).replace(day=1)
        return self._format_period_name(today)

    @api.model
    def _format_period_name(self, period_month):
        period_month = fields.Date.to_date(period_month)
        if not period_month:
            return False
        return babel_format_date(
            period_month,
            format="MMMM y",
            locale="fr_FR",
        ).capitalize()

    @api.model
    def _is_automatic_name(self, name, period_month):
        return (
            not name
            or name.startswith("Platform billing —")
            or name == self._format_period_name(period_month)
        )

    @api.model
    def _is_french_period_name(self, name):
        month_name, separator, year = (name or "").rpartition(" ")
        return bool(
            separator
            and month_name in FRENCH_MONTH_NAMES
            and len(year) == 4
            and year.isdigit(),
        )

    @api.onchange("period_month")
    def _onchange_period_month_name(self):
        for session in self:
            origin_period = session._origin.period_month if session._origin else False
            is_automatic = session._is_automatic_name(
                session.name,
                origin_period,
            )
            if not origin_period:
                is_automatic = is_automatic or session._is_french_period_name(
                    session.name,
                )
            if is_automatic:
                session.name = session._format_period_name(session.period_month)

    @api.depends("generated_move_ids.move_type")
    def _compute_document_links(self):
        for session in self:
            session.customer_invoice_ids = session.generated_move_ids.filtered(
                lambda move: move.move_type == "out_invoice",
            )
            session.vendor_bill_ids = session.generated_move_ids.filtered(
                lambda move: move.move_type == "in_invoice",
            )
            session.compensation_move_ids = session.generated_move_ids.filtered(
                lambda move: move.move_type == "entry",
            )

    @api.depends(
        "payout_ids",
        "payout_ids.platform_id",
        "payout_ids.platform_currency_id",
        "payout_ids.net_platform_amount",
        "payout_ids.gross_platform_amount",
        "payout_ids.commission_platform_amount",
        "payout_ids.bank_allocation_ids",
        "payout_ids.bank_allocation_ids.bank_statement_line_id",
        "payout_ids.bank_allocation_ids.bank_amount",
        "payout_ids.bank_received_amount",
        "generated_move_ids",
    )
    def _compute_summary(self):
        for session in self:
            grouped = defaultdict(
                lambda: {"gross": 0.0, "commission": 0.0, "net": 0.0},
            )
            for payout in session.payout_ids:
                key = (
                    payout.platform_id.name or _("To complete"),
                    payout.platform_currency_id.name or "—",
                )
                grouped[key]["gross"] += payout.gross_platform_amount
                grouped[key]["commission"] += payout.commission_platform_amount
                grouped[key]["net"] += payout.net_platform_amount
            summary = []
            for (platform_name, currency_name), totals in sorted(grouped.items()):
                summary.append(
                    _(
                        "%(platform)s (%(currency)s): gross %(gross).2f, "
                        "commission %(commission).2f, net %(net).2f",
                        platform=platform_name,
                        currency=currency_name,
                        **totals,
                    ),
                )
            session.payout_count = len(session.payout_ids)
            session.generated_move_count = len(session.generated_move_ids)
            session.bank_transaction_count = len(
                session.payout_ids.bank_statement_line_ids,
            )
            session.total_bank_received = sum(
                session.payout_ids.mapped("bank_received_amount"),
            )
            session.currency_summary = "\n".join(summary) or _("No payouts.")

    @api.depends(
        "state",
        "payout_ids.validation_status",
        "generated_move_ids.state",
        "generated_move_ids.payment_state",
    )
    def _compute_guidance(self):
        guidance = {
            "draft": (
                _("Check"),
                _("Complete the payouts, then run the accounting checks."),
            ),
            "ready": (
                _("Generate drafts"),
                _("Create the customer invoices and commission bills."),
            ),
            "generated": (
                _("Post documents"),
                _("Review the drafts before posting and compensating them."),
            ),
            "posted": (
                _("Reconcile bank"),
                _(
                    "Link received bank transactions. Delayed amounts remain "
                    "open until they are received.",
                ),
            ),
            "paid": (
                _("Complete"),
                _("All generated documents and selected bank transactions are settled."),
            ),
            "cancelled": (
                _("Cancelled"),
                _("This session is closed without further processing."),
            ),
        }
        for session in self:
            session.next_action, session.guidance = guidance[session.state]

    @api.depends("company_id", "payout_ids.platform_id")
    def _compute_platform_coverage(self):
        Platform = self.env["usl.platform.billing.platform"]
        for session in self:
            active_platforms = Platform.search(
                [
                    ("company_id", "=", session.company_id.id),
                    ("active", "=", True),
                ],
            )
            missing = active_platforms - session.payout_ids.platform_id
            session.missing_active_platform_ids = missing
            session.platform_coverage_warning = (
                _(
                    "No payout is registered for these active platforms: %(platforms)s. "
                    "Monthly billing should normally cover every active platform; "
                    "you may continue after confirming the exception.",
                    platforms=", ".join(missing.mapped("display_name")),
                )
                if missing
                else False
            )

    @api.constrains("period_month")
    def _check_period_first_day(self):
        for session in self:
            if session.period_month and session.period_month.day != 1:
                raise ValidationError(_("The billing period must be the first day of a month."))

    @api.constrains("company_id", "payout_ids")
    def _check_payout_companies(self):
        for session in self:
            if session.payout_ids.filtered(
                lambda payout: (
                    payout.platform_id
                    and payout.platform_id.company_id != session.company_id
                ),
            ):
                raise ValidationError(_("All payouts must belong to the session company."))

    @api.model_create_multi
    def create(self, vals_list):
        normalized_values = []
        default_period = fields.Date.context_today(self).replace(day=1)
        for incoming_values in vals_list:
            values = dict(incoming_values)
            if not self.env.su:
                for field_name, default_value in SESSION_WORKFLOW_DEFAULTS.items():
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
            period_month = fields.Date.to_date(
                values.get("period_month") or default_period,
            )
            if self._is_automatic_name(values.get("name"), default_period):
                values["name"] = self._format_period_name(period_month)
            normalized_values.append(values)
        sessions = super().create(normalized_values)
        for session in sessions:
            session.action_prefill()
        return sessions

    def _strip_unchanged_workflow_values(self, values):
        values = dict(values)
        if self.env.su:
            return values
        for field_name in SESSION_WORKFLOW_DEFAULTS.keys() & values.keys():
            field = self._fields[field_name]
            for session in self:
                session[field_name]
                submitted = field.convert_to_cache(values[field_name], session)
                current = session._cache[field_name]
                if submitted != current and (submitted or current):
                    raise AccessError(
                        _("Workflow fields can only be changed by app actions."),
                    )
            values.pop(field_name)
        return values

    def write(self, vals):
        vals = self._strip_unchanged_workflow_values(vals)
        basis = {"company_id", "period_month", "invoice_date", "due_date", "bank_currency_id"}
        if basis & set(vals) and self.filtered(
            lambda session: session.state not in {"draft", "ready"},
        ):
            raise UserError(_("Generated sessions cannot change their accounting basis."))
        if "period_month" in vals and "name" not in vals:
            automatic_sessions = self.filtered(
                lambda session: session._is_automatic_name(
                    session.name,
                    session.period_month,
                ),
            )
            manual_sessions = self - automatic_sessions
            result = True
            for session in automatic_sessions:
                session_values = {
                    **vals,
                    "name": session._format_period_name(vals["period_month"]),
                }
                result = (
                    super(UslPlatformBillingSession, session).write(session_values)
                    and result
                )
            if manual_sessions:
                result = (
                    super(UslPlatformBillingSession, manual_sessions).write(vals)
                    and result
                )
            return result
        return super().write(vals)

    def _workflow_write(self, values):
        return super().write(values)

    def unlink(self):
        if self.filtered(lambda session: session.state not in {"draft", "cancelled"}):
            raise UserError(_("Only draft or cancelled sessions can be deleted."))
        if (
            not self.env.su
            and not self.env.user.has_group(
                "usl_platform_billing.group_platform_billing_manager",
            )
        ):
            raise AccessError(
                _("Only Platform Billing administrators can delete sessions."),
            )
        # Delete children through their ORM lifecycle while their parent still
        # exists.  Relying on the database cascade leaves cached payout records
        # behind while stored dependencies are recomputed, which can surface as
        # a misleading MissingError during cancelled-session deletion.
        self.payout_ids.unlink()
        return super().unlink()

    def _check_operator(self):
        if self.env.su:
            return
        user = self.env.user
        if not user.has_group(
            "usl_platform_billing.group_platform_billing_operator",
        ):
            raise AccessError(_("Only Platform Billing operators can run this action."))
        for session in self:
            if session.company_id not in user.company_ids:
                raise AccessError(_("You cannot operate a session from another company."))

    def _check_reader(self):
        if self.env.su:
            return
        user = self.env.user
        if not user.has_group(
            "usl_platform_billing.group_platform_billing_reader",
        ):
            raise AccessError(_("Only Platform Billing users can inspect this session."))
        for session in self:
            if session.company_id not in user.company_ids:
                raise AccessError(_("You cannot inspect a session from another company."))

    def _period_bounds(self):
        self.ensure_one()
        start = self.period_month
        return start, start + relativedelta(months=1, days=-1)

    def action_prefill(self):
        self._check_operator()
        for session in self:
            if session.state not in {"draft", "ready"}:
                continue
            _, end = session._period_bounds()
            values = {}
            if not session.invoice_date:
                values["invoice_date"] = end
            desired_name = session._format_period_name(session.period_month)
            if (
                session._is_automatic_name(session.name, session.period_month)
                and session.name != desired_name
            ):
                values["name"] = desired_name
            if values:
                session.write(values)
            for payout in session.payout_ids:
                payout_values = {}
                if not payout.platform_currency_id and payout.platform_id:
                    payout_values["platform_currency_id"] = payout.platform_id.currency_id.id
                if not payout.commission_rate_snapshot and payout.platform_id:
                    payout_values["commission_rate_snapshot"] = (
                        payout.platform_id.commission_rate
                    )
                if payout_values:
                    payout.write(payout_values)
        return True

    def _validation_errors(self):
        self.ensure_one()
        errors = []
        if not self.payout_ids:
            errors.append(_("Add at least one payout."))
        invalid = self.payout_ids.filtered(
            lambda payout: payout.validation_status == "error",
        )
        for payout in invalid:
            errors.append(
                _(
                    "%(payout)s: %(message)s",
                    payout=payout.display_name,
                    message=payout.validation_message,
                ),
            )
        for platform in self.payout_ids.platform_id:
            missing = []
            for field_name in (
                "partner_id",
                "currency_id",
                "revenue_product_id",
                "commission_product_id",
                "sale_journal_id",
                "purchase_journal_id",
            ):
                if not platform[field_name]:
                    missing.append(platform._fields[field_name].string)
            if platform.auto_create_compensation and not platform.compensation_journal_id:
                missing.append(_("Compensation Journal"))
            if missing:
                errors.append(
                    _(
                        "%(platform)s is missing: %(fields)s",
                        platform=platform.display_name,
                        fields=", ".join(missing),
                    ),
                )
            errors.extend(
                _(
                    "%(platform)s: %(message)s",
                    platform=platform.display_name,
                    message=message,
                )
                for message in platform._account_configuration_errors()
            )
        return errors

    def action_check(self):
        self._check_operator()
        for session in self:
            if session.state not in {"draft", "ready"}:
                raise UserError(_("Only draft sessions can be checked."))
            session.action_prefill()
            errors = session._validation_errors()
            if errors:
                session.message_post(
                    body=_("Platform billing check failed:<br/>%s", "<br/>".join(errors)),
                    subtype_xmlid="mail.mt_note",
                )
                raise UserError("\n".join(errors))
            session._workflow_write({"state": "ready"})
            session.message_post(
                body=_("Accounting checks passed. The session is ready to generate."),
                subtype_xmlid="mail.mt_note",
            )
        return True

    def _invoice_line_values(self, payout, product, amount, label):
        values = {
            "product_id": product.id,
            "name": label,
            "quantity": 1.0,
            "price_unit": amount,
        }
        if payout.platform_id.analytic_distribution:
            values["analytic_distribution"] = payout.platform_id.analytic_distribution
        return values

    def _document_valuation_groups(self, payouts):
        groups = defaultdict(lambda: self.env["usl.platform.billing.payout"])
        for payout in payouts.sorted(key=lambda item: item.id):
            if payout.currency_valuation_method == "bank":
                key = ("bank", f"{payout.effective_bank_rate:.10f}")
            else:
                key = ("reference", "")
            groups[key] |= payout
        return list(groups.values())

    def _bank_invoice_currency_rate(self, payouts):
        bank_payouts = payouts.filtered(
            lambda payout: payout.currency_valuation_method == "bank",
        )
        if not bank_payouts:
            return False
        if len(bank_payouts) != len(payouts):
            raise UserError(
                _("Bank-rate and reference-rate payouts require separate documents."),
            )
        rates = {f"{payout.effective_bank_rate:.10f}" for payout in bank_payouts}
        if len(rates) != 1 or not bank_payouts[0].effective_bank_rate:
            raise UserError(
                _("Bank-rate payouts with different effective rates require separate documents."),
            )
        if bank_payouts.platform_currency_id == self.company_id.currency_id:
            return 1.0
        return 1.0 / bank_payouts[0].effective_bank_rate

    def _move_values(
        self,
        platform,
        move_type,
        payouts,
        line_commands,
        *,
        invoice_currency_rate=False,
    ):
        partner = (
            platform.customer_partner
            if move_type == "out_invoice"
            else platform.supplier_partner
        )
        journal = (
            platform.sale_journal_id
            if move_type == "out_invoice"
            else platform.purchase_journal_id
        )
        references = ", ".join(payouts.mapped("platform_reference"))
        values = {
            "move_type": move_type,
            "company_id": self.company_id.id,
            "journal_id": journal.id,
            "partner_id": partner.id,
            "currency_id": platform.currency_id.id,
            "invoice_date": self.invoice_date,
            "ref": references,
            "platform_billing_session_id": self.id,
            "platform_billing_platform_id": platform.id,
            "platform_billing_payout_ids": [Command.set(payouts.ids)],
            "invoice_line_ids": line_commands,
        }
        if invoice_currency_rate:
            values["invoice_currency_rate"] = invoice_currency_rate
        if self.due_date:
            values.update(
                {
                    "invoice_payment_term_id": False,
                    "invoice_date_due": self.due_date,
                },
            )
        return values

    def _copy_supporting_documents(self, payouts, moves):
        attachments = payouts.attachment_ids
        attachments.check_access("read")
        for attachment in attachments:
            for move in moves:
                existing = self.env["ir.attachment"].search_count(
                    [
                        ("res_model", "=", "account.move"),
                        ("res_id", "=", move.id),
                        ("name", "=", attachment.name),
                        ("checksum", "=", attachment.checksum),
                    ],
                )
                if not existing:
                    attachment.copy(
                        {
                            "res_model": "account.move",
                            "res_id": move.id,
                        },
                    )

    def _generate_platform_documents(self, platform, payouts):
        generated_moves = self.env["account.move"]
        for invoice_payouts in self._document_valuation_groups(payouts):
            invoice_rate = self._bank_invoice_currency_rate(invoice_payouts)
            invoice_lines = [
                Command.create(
                    self._invoice_line_values(
                        payout,
                        platform.revenue_product_id,
                        payout.gross_platform_amount,
                        _("Gross platform revenue — %s", payout.platform_reference),
                    ),
                )
                for payout in invoice_payouts
            ]
            invoice = self.env["account.move"].create(
                self._move_values(
                    platform,
                    "out_invoice",
                    invoice_payouts,
                    invoice_lines,
                    invoice_currency_rate=invoice_rate,
                ),
            )
            if platform.vendor_bill_grouping_mode == "monthly":
                bill_groups = [invoice_payouts]
            else:
                bill_groups = list(invoice_payouts)
            bills = self.env["account.move"]
            for bill_payouts in bill_groups:
                bill_lines = [
                    Command.create(
                        self._invoice_line_values(
                            payout,
                            platform.commission_product_id,
                            payout.commission_platform_amount,
                            _("Platform commission — %s", payout.platform_reference),
                        ),
                    )
                    for payout in bill_payouts
                ]
                bills |= self.env["account.move"].create(
                    self._move_values(
                        platform,
                        "in_invoice",
                        bill_payouts,
                        bill_lines,
                        invoice_currency_rate=(
                            self._bank_invoice_currency_rate(bill_payouts)
                        ),
                    ),
                )
            for payout in invoice_payouts:
                payout._workflow_write(
                    {
                        "customer_invoice_id": invoice.id,
                        "vendor_bill_id": bills.filtered(
                            lambda bill, payout=payout: payout
                            in bill.platform_billing_payout_ids,
                        )[:1].id,
                        "state": "generated",
                    },
                )
            self._copy_supporting_documents(invoice_payouts, invoice | bills)
            generated_moves |= invoice | bills
        # Incomplete monthly coverage always requires the explicit posting
        # confirmation, even when this platform normally auto-posts.
        if platform.auto_post_invoices and not self.missing_active_platform_ids:
            generated_moves.action_post()

    def action_generate_documents(self):
        self._check_operator()
        for session in self:
            if session.state != "ready":
                raise UserError(_("Check the session before generating documents."))
            if session.generated_move_ids:
                raise UserError(_("This session already has generated documents."))
            errors = session._validation_errors()
            if errors:
                raise UserError("\n".join(errors))
            for platform in session.payout_ids.platform_id:
                session._generate_platform_documents(
                    platform,
                    session.payout_ids.filtered(
                        lambda payout, platform=platform: payout.platform_id == platform,
                    ),
                )
            session._workflow_write(
                {
                    "state": "generated",
                    "generated_at": fields.Datetime.now(),
                    "generated_by_id": self.env.user.id,
                },
            )
            session.message_post(
                body=_("Customer invoices and commission bills were generated."),
                subtype_xmlid="mail.mt_note",
            )
        return True

    def _create_compensation_move(self, platform, payouts):
        moves = self.env["account.move"]
        company_currency = self.company_id.currency_id
        supplier = platform.supplier_partner.with_company(self.company_id)
        customer = platform.customer_partner.with_company(self.company_id)
        payable = supplier.property_account_payable_id
        receivable = customer.property_account_receivable_id
        if not payable or not receivable:
            raise UserError(
                _("Configure receivable and payable accounts on %(platform)s partners.", platform=platform.display_name),
            )
        currency_id = platform.currency_id.id
        for payout in payouts.sorted(key=lambda item: item.id):
            if payout.compensation_move_id:
                moves |= payout.compensation_move_id
                continue
            amount_currency = payout.commission_platform_amount
            if platform.currency_id.is_zero(amount_currency):
                continue
            if payout.currency_valuation_method == "bank":
                balance = company_currency.round(
                    amount_currency * payout.effective_bank_rate,
                )
            else:
                balance = platform.currency_id._convert(
                    amount_currency,
                    company_currency,
                    self.company_id,
                    self.invoice_date,
                )
            balance = company_currency.round(balance)
            move = self.env["account.move"].create(
                {
                    "move_type": "entry",
                    "company_id": self.company_id.id,
                    "currency_id": platform.currency_id.id,
                    "journal_id": platform.compensation_journal_id.id,
                    "date": self.invoice_date,
                    "ref": _(
                        "Platform commission compensation — %(platform)s — %(reference)s",
                        platform=platform.name,
                        reference=payout.platform_reference,
                    ),
                    "platform_billing_session_id": self.id,
                    "platform_billing_platform_id": platform.id,
                    "platform_billing_payout_ids": [Command.set(payout.ids)],
                    "line_ids": [
                        Command.create(
                            {
                                "name": _("Commission payable compensation"),
                                "partner_id": supplier.id,
                                "account_id": payable.id,
                                "debit": balance,
                                "credit": 0.0,
                                "currency_id": currency_id,
                                "amount_currency": (
                                    amount_currency
                                    if platform.currency_id != company_currency
                                    else balance
                                ),
                            },
                        ),
                        Command.create(
                            {
                                "name": _("Commission receivable compensation"),
                                "partner_id": customer.id,
                                "account_id": receivable.id,
                                "debit": 0.0,
                                "credit": balance,
                                "currency_id": currency_id,
                                "amount_currency": (
                                    -amount_currency
                                    if platform.currency_id != company_currency
                                    else -balance
                                ),
                            },
                        ),
                    ],
                },
            )
            payout._workflow_write({"compensation_move_id": move.id})
            moves |= move
        return moves

    def _reconcile_compensation(self, platform, payouts, compensation):
        bills = payouts.vendor_bill_id.filtered(lambda move: move.state == "posted")
        invoices = payouts.customer_invoice_id.filtered(lambda move: move.state == "posted")
        payable_lines = (bills.line_ids | compensation.line_ids).filtered(
            lambda line: (
                line.account_id.account_type == "liability_payable"
                and line.partner_id.commercial_partner_id
                == platform.supplier_partner.commercial_partner_id
                and not line.reconciled
            ),
        )
        if payable_lines:
            payable_lines.reconcile()
        receivable_lines = (invoices.line_ids | compensation.line_ids).filtered(
            lambda line: (
                line.account_id.account_type == "asset_receivable"
                and line.partner_id.commercial_partner_id
                == platform.customer_partner.commercial_partner_id
                and not line.reconciled
            ),
        )
        if receivable_lines:
            receivable_lines.reconcile()

    def _repair_legacy_grouped_compensations(self):
        """Replace pre-fix pooled compensation with exact payout entries.

        This private maintenance operation is deliberately not run on upgrade.
        It is safe only before any linked bank receipt has been reconciled and
        leaves Odoo's reversal trail for exchange differences created by the
        former pooled reconciliation.
        """
        self._check_operator()
        repaired = self.env["account.move"]
        for session in self:
            legacy_moves = session.compensation_move_ids.filtered(
                lambda move: (
                    len(move.platform_billing_payout_ids) > 1
                    and any(
                        payout.compensation_move_id == move
                        and payout.currency_valuation_method == "bank"
                        for payout in move.platform_billing_payout_ids
                    )
                ),
            )
            if not legacy_moves:
                continue
            if session.state != "posted":
                raise UserError(
                    _("Only a posted, unpaid session can repair pooled compensation."),
                )
            payouts = legacy_moves.platform_billing_payout_ids
            if payouts.bank_allocation_ids.bank_statement_line_id.filtered(
                "is_reconciled",
            ):
                raise UserError(
                    _(
                        "Unmatch the session's bank receipts before repairing pooled "
                        "compensation.",
                    ),
                )
            if legacy_moves.filtered("inalterable_hash") or any(
                move._get_violated_lock_dates(move.date, False)
                for move in legacy_moves
            ):
                raise UserError(
                    _(
                        "The pooled compensation is protected by an accounting lock or "
                        "secure hash and cannot be rebuilt.",
                    ),
                )
            reversal_values = [
                {
                    "date": move.date,
                    "ref": _(
                        "Correction of legacy pooled compensation — %s",
                        move.name,
                    ),
                }
                for move in legacy_moves
            ]
            legacy_moves._reverse_moves(reversal_values, cancel=True)
            payouts._workflow_write({"compensation_move_id": False})
            for platform in payouts.platform_id:
                platform_payouts = payouts.filtered(
                    lambda payout, platform=platform: payout.platform_id == platform,
                )
                compensations = session._create_compensation_move(
                    platform,
                    platform_payouts,
                )
                compensations.action_post()
                repaired |= compensations
                for payout in platform_payouts:
                    session._reconcile_compensation(
                        platform,
                        payout,
                        payout.compensation_move_id,
                    )
            session._refresh_state()
            session.message_post(
                body=_(
                    "Legacy pooled commission compensation was rebuilt per payout. "
                    "The former exchange-difference entries were reversed through "
                    "Odoo's accounting trail.",
                ),
                subtype_xmlid="mail.mt_note",
            )
        return repaired

    def action_post_documents(self):
        self._check_operator()
        for session in self:
            if session.state != "generated":
                raise UserError(_("Only generated sessions can be posted."))
            if (
                session.missing_active_platform_ids
                and not self.env.context.get("skip_platform_coverage_warning")
            ):
                wizard = self.env[
                    "usl.platform.billing.post.confirm.wizard"
                ].create(
                    {
                        "session_id": session.id,
                        "missing_platform_ids": [
                            Command.set(session.missing_active_platform_ids.ids),
                        ],
                        "warning_message": session.platform_coverage_warning,
                    },
                )
                return {
                    "type": "ir.actions.act_window",
                    "name": _("Confirm Incomplete Platform Coverage"),
                    "res_model": wizard._name,
                    "res_id": wizard.id,
                    "view_mode": "form",
                    "target": "new",
                }
            drafts = session.generated_move_ids.filtered(
                lambda move: move.state == "draft",
            )
            invoices_and_bills = drafts.filtered(
                lambda move: move.move_type in {"out_invoice", "in_invoice"},
            )
            if invoices_and_bills:
                invoices_and_bills.action_post()
            for platform in session.payout_ids.platform_id:
                payouts = session.payout_ids.filtered(
                    lambda payout, platform=platform: payout.platform_id == platform,
                )
                if platform.auto_create_compensation:
                    compensations = session._create_compensation_move(platform, payouts)
                    compensations.filtered(
                        lambda move: move.state == "draft",
                    ).action_post()
                    for payout in payouts:
                        session._reconcile_compensation(
                            platform,
                            payout,
                            payout.compensation_move_id,
                        )
            session.payout_ids._workflow_write({"state": "posted"})
            session._workflow_write({"state": "posted"})
            session._refresh_state()
            session.message_post(
                body=_("Documents were posted and configured compensations reconciled."),
                subtype_xmlid="mail.mt_note",
            )
        return True

    def action_reset_drafts(self):
        self._check_operator()
        for session in self:
            if session.state != "generated":
                raise UserError(_("Only generated draft sessions can be reset."))
            if session.generated_move_ids.filtered(lambda move: move.state != "draft"):
                raise UserError(_("Posted journal entries cannot be reset from this app."))
            moves = session.generated_move_ids
            session.payout_ids._workflow_write(
                {
                    "customer_invoice_id": False,
                    "vendor_bill_id": False,
                    "compensation_move_id": False,
                    "state": "draft",
                },
            )
            moves.unlink()
            session._workflow_write(
                {
                    "state": "ready",
                    "generated_at": False,
                    "generated_by_id": False,
                },
            )
            session.message_post(
                body=_("Generated drafts were deleted; payout inputs were preserved."),
                subtype_xmlid="mail.mt_note",
            )
        return True

    def action_open_generated_moves(self):
        self.ensure_one()
        self._check_reader()
        action = self.env["ir.actions.actions"]._for_xml_id(
            "usl_platform_billing.action_platform_billing_moves",
        )
        action["domain"] = [("id", "in", self.generated_move_ids.ids)]
        return action

    def action_open_bank_transactions(self):
        self.ensure_one()
        self._check_reader()
        return {
            "type": "ir.actions.act_window",
            "name": _("Platform Bank Transactions"),
            "res_model": "account.bank.statement.line",
            "view_mode": "list,form",
            "domain": [
                ("id", "in", self.payout_ids.bank_statement_line_ids.ids),
            ],
        }

    def action_open_bank_import(self):
        self.ensure_one()
        self._check_operator()
        if self.state not in {"draft", "ready", "generated", "posted"}:
            raise UserError(
                _(
                    "Bank transactions can only be linked to draft, ready, "
                    "generated or posted sessions.",
                ),
            )
        wizard = self.env["usl.platform.billing.bank.import.wizard"].create(
            {
                "session_id": self.id,
                "mode": "link" if self.payout_ids else "create",
            },
        )
        wizard._populate_payout_candidates()
        wizard._populate_candidates()
        return {
            "type": "ir.actions.act_window",
            "name": _("Import Bank Transactions"),
            "res_model": wizard._name,
            "res_id": wizard.id,
            "view_mode": "form",
            "target": "new",
        }

    def _refresh_state(self):
        for session in self:
            if session.state == "cancelled":
                continue
            moves = session.generated_move_ids
            if not moves:
                session._workflow_write(
                    {
                        "state": (
                            "ready"
                            if session.payout_ids
                            and not session._validation_errors()
                            else "draft"
                        ),
                    },
                )
                continue
            if moves.filtered(lambda move: move.state == "draft"):
                if session.state != "generated":
                    session._workflow_write({"state": "generated"})
                continue
            active_payouts = session.payout_ids.filtered(
                lambda payout: payout.state != "cancelled",
            )
            settled_payouts = active_payouts.filtered(
                lambda payout: payout._accounting_is_settled(),
            )
            open_payouts = active_payouts - settled_payouts
            settled_payouts.filtered(
                lambda payout: payout.state != "paid",
            )._workflow_write({"state": "paid"})
            open_payouts.filtered(
                lambda payout: payout.state != "posted",
            )._workflow_write({"state": "posted"})
            state = (
                "paid"
                if active_payouts and not open_payouts
                else "posted"
            )
            if session.state != state:
                session._workflow_write({"state": state})

    def action_reconcile_bank(self):
        self._check_operator()
        for session in self:
            if session.state not in {"posted", "paid"}:
                raise UserError(_("Post the generated documents before bank reconciliation."))
            blocked = []
            affected_sessions = session
            linked_allocations = session.payout_ids.bank_allocation_ids
            for bank_line in linked_allocations.bank_statement_line_id:
                allocations = self.env[
                    "usl.platform.billing.bank.allocation"
                ].search(
                    [("bank_statement_line_id", "=", bank_line.id)],
                )
                affected_sessions |= allocations.session_id
                try:
                    with self.env.cr.savepoint():
                        allocations._reconcile_bank_transaction()
                except (UserError, ValidationError) as error:
                    allocations._action_write({"blocked_reason": str(error)})
                    blocked.append(
                        f"{bank_line.display_name}: {error}",
                    )
            affected_sessions._refresh_state()
            body = _("Bank reconciliation finished.")
            delayed_count = len(
                session.payout_ids.filtered(
                    lambda payout: (
                        payout.state != "cancelled"
                        and not payout._accounting_is_settled()
                    ),
                ),
            )
            if delayed_count:
                body += "<br/>" + _(
                    "%(count)s payout(s) remain open while payment is delayed.",
                    count=delayed_count,
                )
            evidence_count = len(
                session.payout_ids.filtered(
                    lambda payout: (
                        payout.state != "cancelled"
                        and payout._accounting_is_settled()
                        and payout.bank_match_status != "reconciled"
                    ),
                ),
            )
            if evidence_count:
                body += "<br/>" + _(
                    "%(count)s paid payout(s) do not have complete Platform Billing "
                    "bank evidence; native Accounting settlement remains authoritative.",
                    count=evidence_count,
                )
            if blocked:
                body += "<br/>" + "<br/>".join(blocked)
            session.message_post(body=body, subtype_xmlid="mail.mt_note")
        return {"type": "ir.actions.client", "tag": "soft_reload"}

    def action_cancel(self):
        self._check_operator()
        for session in self:
            if session.state not in {"draft", "ready"}:
                raise UserError(_("Only draft or ready sessions can be cancelled."))
            session.payout_ids._workflow_write({"state": "cancelled"})
            session._workflow_write({"state": "cancelled"})
        return True
