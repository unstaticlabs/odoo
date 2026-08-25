import calendar
import hashlib
from datetime import datetime, time
from itertools import chain

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .constants import REVIEW_STATES, SOURCE_PROVIDERS


class B2cAccountingSession(models.Model):
    _name = "b2c.accounting.session"
    _description = "Monthly B2C Accounting Session"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "period_start desc, company_id, id desc"
    _check_company_auto = True

    name = fields.Char(compute="_compute_identity", store=True, index=True)
    session_key = fields.Char(compute="_compute_identity", store=True, index=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
        ondelete="cascade",
    )
    channel_id = fields.Many2one(
        "b2c.channel",
        check_company=True,
        index=True,
        ondelete="restrict",
        help="Leave empty for the complete company/channel perimeter.",
    )
    source_provider = fields.Selection(
        SOURCE_PROVIDERS,
        index=True,
        help="Leave empty for all commerce and processor providers.",
    )
    period_start = fields.Date(
        required=True,
        default=lambda self: fields.Date.start_of(
            fields.Date.context_today(self),
            "month",
        ),
    )
    period_end = fields.Date(compute="_compute_period_end", store=True)
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("reviewed", "Reviewed"),
            ("locked", "Locked"),
        ],
        required=True,
        default="draft",
        index=True,
        tracking=True,
    )
    company_currency_id = fields.Many2one(
        related="company_id.currency_id",
        store=True,
    )
    order_count = fields.Integer(readonly=True)
    units_sold = fields.Float(readonly=True, digits="Product Unit")
    revenue_company_amount = fields.Monetary(
        currency_field="company_currency_id",
        readonly=True,
    )
    refund_company_amount = fields.Monetary(
        currency_field="company_currency_id",
        readonly=True,
    )
    fee_company_amount = fields.Monetary(
        currency_field="company_currency_id",
        readonly=True,
    )
    cogs_company_amount = fields.Monetary(
        currency_field="company_currency_id",
        readonly=True,
    )
    gross_margin_company_amount = fields.Monetary(
        currency_field="company_currency_id",
        readonly=True,
    )
    unallocated_revenue_company_amount = fields.Monetary(
        currency_field="company_currency_id",
        readonly=True,
    )
    unknown_amount_company_amount = fields.Monetary(
        currency_field="company_currency_id",
        readonly=True,
    )
    line_revenue_coverage_percent = fields.Float(
        string="Line Revenue Coverage (%)",
        readonly=True,
    )
    accounting_link_coverage_percent = fields.Float(
        string="Accounting Link Coverage (%)",
        readonly=True,
    )
    pending_mapping_count = fields.Integer(readonly=True)
    pending_link_count = fields.Integer(readonly=True)
    pending_conversion_count = fields.Integer(
        readonly=True,
        help=(
            "Records whose transaction-currency value cannot yet be represented "
            "as an evidenced historical company-currency value."
        ),
    )
    refreshed_at = fields.Datetime(readonly=True, copy=False)
    reviewed_by_id = fields.Many2one("res.users", readonly=True, copy=False)
    reviewed_at = fields.Datetime(readonly=True, copy=False)
    locked_by_id = fields.Many2one("res.users", readonly=True, copy=False)
    locked_at = fields.Datetime(readonly=True, copy=False)
    review_note = fields.Text()
    accounting_link_ids = fields.One2many(
        "b2c.accounting.link",
        "session_id",
        string="Session Accounting Evidence",
    )

    _company_session_key_unique = models.Constraint(
        "UNIQUE(company_id, session_key)",
        "A monthly B2C accounting session scope must be unique per company.",
    )

    @api.depends("company_id", "channel_id", "source_provider", "period_start")
    def _compute_identity(self):
        for session in self:
            month = session.period_start.strftime("%Y-%m") if session.period_start else "pending"
            channel = session.channel_id.code if session.channel_id else "all-channels"
            provider = session.source_provider or "all-providers"
            session.name = f"B2C {month} · {channel} · {provider}"
            session.session_key = f"{month}:{channel}:{provider}"

    @api.depends("period_start")
    def _compute_period_end(self):
        for session in self:
            if not session.period_start:
                session.period_end = False
                continue
            last_day = calendar.monthrange(
                session.period_start.year,
                session.period_start.month,
            )[1]
            session.period_end = session.period_start.replace(day=last_day)

    @api.constrains("period_start")
    def _check_period_start(self):
        for session in self:
            if session.period_start and session.period_start.day != 1:
                raise ValidationError(
                    self.env._("A B2C accounting session must start on the first day of a month."),
                )

    def _scope_domain(self, date_field):
        self.ensure_one()
        start = datetime.combine(self.period_start, time.min)
        end = datetime.combine(self.period_end, time.max)
        domain = [
            ("company_id", "=", self.company_id.id),
            (date_field, ">=", start),
            (date_field, "<=", end),
        ]
        if self.channel_id:
            domain.append(("channel_id", "=", self.channel_id.id))
        if self.source_provider:
            domain.append(("source_provider", "=", self.source_provider))
        return domain

    def action_refresh(self):
        for session in self:
            if session.state == "locked":
                raise UserError(self.env._("Unlock the session before refreshing it."))
            orders = self.env["b2c.order"].search(session._scope_domain("order_date"))
            lines = self.env["b2c.order.line"].search(
                session._scope_domain("order_date"),
            )
            payments = self.env["b2c.payment.event"].search(
                session._scope_domain("event_date"),
            )
            fulfilments = self.env["b2c.fulfilment.event"].search(
                session._scope_domain("event_date"),
            )
            revenue = sum(orders.mapped("revenue_company_amount"))
            refunds = sum(payments.mapped("refund_company_amount"))
            fees = sum(payments.mapped("fee_company_amount"))
            cogs = sum(fulfilments.mapped("company_cogs_amount"))
            allocated = sum(lines.mapped("revenue_company_amount"))
            relevant = len(orders) + len(payments) + len(fulfilments)
            linked = sum(
                bool(
                    record.accounting_link_ids.filtered(
                        lambda link: link.link_state == "verified",
                    ),
                )
                for record in chain(orders, payments, fulfilments)
            )
            unknown = sum(
                abs(order.revenue_company_amount)
                for order in orders
                if order.amount_completeness != "complete"
            ) + sum(
                abs(event.net_company_amount)
                for event in payments
                if event.completeness_state != "complete"
            ) + sum(
                abs(event.company_cogs_amount)
                for event in fulfilments
                if event.completeness_state != "complete"
            )
            session.write(
                {
                    "order_count": len(orders),
                    "units_sold": sum(lines.mapped("quantity")),
                    "revenue_company_amount": revenue,
                    "refund_company_amount": refunds,
                    "fee_company_amount": fees,
                    "cogs_company_amount": cogs,
                    "gross_margin_company_amount": revenue + refunds - fees - cogs,
                    "unallocated_revenue_company_amount": revenue - allocated,
                    "unknown_amount_company_amount": unknown,
                    "line_revenue_coverage_percent": (
                        min(100.0, abs(allocated) / abs(revenue) * 100.0)
                        if revenue
                        else 0.0
                    ),
                    "accounting_link_coverage_percent": (
                        linked / relevant * 100.0 if relevant else 0.0
                    ),
                    "pending_mapping_count": len(
                        lines.filtered(lambda line: line.mapping_state == "pending"),
                    ),
                    "pending_link_count": relevant - linked,
                    "pending_conversion_count": len(
                        orders.filtered(lambda order: order.conversion_state == "pending"),
                    )
                    + len(
                        payments.filtered(lambda event: event.conversion_state == "pending"),
                    )
                    + len(
                        fulfilments.filtered(
                            lambda event: event.conversion_state == "pending",
                        ),
                    ),
                    "refreshed_at": fields.Datetime.now(),
                    "state": "draft",
                    "reviewed_by_id": False,
                    "reviewed_at": False,
                },
            )

    def action_mark_reviewed(self):
        for session in self:
            if not session.refreshed_at:
                raise UserError(self.env._("Refresh the session before reviewing it."))
            session.write(
                {
                    "state": "reviewed",
                    "reviewed_by_id": self.env.user.id,
                    "reviewed_at": fields.Datetime.now(),
                },
            )

    def action_lock(self):
        for session in self:
            if session.state != "reviewed":
                raise UserError(self.env._("Review the session before locking it."))
            session.write(
                {
                    "state": "locked",
                    "locked_by_id": self.env.user.id,
                    "locked_at": fields.Datetime.now(),
                },
            )

    def action_unlock(self):
        if not self.env.user.has_group("usl_b2c.group_b2c_manager"):
            raise AccessError(self.env._("Only a B2C manager can unlock a session."))
        self.with_context(b2c_unlock=True).write(
            {"state": "reviewed", "locked_by_id": False, "locked_at": False},
        )

    def write(self, vals):
        if self.filtered(lambda session: session.state == "locked") and not self.env.context.get(
            "b2c_unlock",
        ):
            raise UserError(self.env._("Locked B2C accounting sessions are immutable."))
        return super().write(vals)

    def unlink(self):
        if self.filtered(lambda session: session.state != "draft"):
            raise UserError(self.env._("Only draft B2C accounting sessions can be deleted."))
        return super().unlink()


class B2cAccountingLink(models.Model):
    _name = "b2c.accounting.link"
    _description = "B2C Accounting and Bank Evidence Link"
    _order = "company_id, link_state, id"
    _check_company_auto = True

    name = fields.Char(required=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
        ondelete="cascade",
    )
    link_key = fields.Char(compute="_compute_link_key", store=True, index=True)
    link_type = fields.Selection(
        [
            ("revenue", "Revenue"),
            ("refund", "Refund"),
            ("fee", "Fee"),
            ("payout", "Payout"),
            ("bank", "Bank"),
            ("clearing", "Clearing"),
            ("supplier_cost", "Supplier cost"),
            ("cogs", "COGS"),
            ("supporting", "Supporting evidence"),
        ],
        required=True,
        index=True,
    )
    link_state = fields.Selection(
        REVIEW_STATES + [("verified", "Verified"), ("rejected", "Rejected")],
        required=True,
        default="pending",
        index=True,
    )
    order_id = fields.Many2one(
        "b2c.order",
        check_company=True,
        ondelete="cascade",
        index=True,
    )
    payment_event_id = fields.Many2one(
        "b2c.payment.event",
        check_company=True,
        ondelete="cascade",
        index=True,
    )
    fulfilment_event_id = fields.Many2one(
        "b2c.fulfilment.event",
        check_company=True,
        ondelete="cascade",
        index=True,
    )
    session_id = fields.Many2one(
        "b2c.accounting.session",
        check_company=True,
        ondelete="cascade",
        index=True,
    )
    account_move_id = fields.Many2one(
        "account.move",
        check_company=True,
        ondelete="restrict",
        index=True,
    )
    account_move_line_id = fields.Many2one(
        "account.move.line",
        check_company=True,
        ondelete="restrict",
        index=True,
    )
    bank_statement_line_id = fields.Many2one(
        "account.bank.statement.line",
        string="Bank Transaction",
        check_company=True,
        ondelete="restrict",
        index=True,
    )
    account_payment_id = fields.Many2one(
        "account.payment",
        check_company=True,
        ondelete="restrict",
        index=True,
    )
    payment_transaction_id = fields.Many2one(
        "payment.transaction",
        check_company=True,
        ondelete="restrict",
        index=True,
    )
    sale_order_id = fields.Many2one(
        "sale.order",
        check_company=True,
        ondelete="restrict",
        index=True,
    )
    stock_picking_id = fields.Many2one(
        "stock.picking",
        check_company=True,
        ondelete="restrict",
        index=True,
    )
    stock_move_id = fields.Many2one(
        "stock.move",
        check_company=True,
        ondelete="restrict",
        index=True,
    )
    attachment_id = fields.Many2one(
        "ir.attachment",
        check_company=True,
        ondelete="restrict",
    )
    evidence_note = fields.Text()
    reviewed_by_id = fields.Many2one("res.users", readonly=True, copy=False)
    reviewed_at = fields.Datetime(readonly=True, copy=False)

    _company_link_key_unique = models.Constraint(
        "UNIQUE(company_id, link_key)",
        "The same B2C accounting evidence link cannot be recorded twice.",
    )

    @api.depends(
        "link_type",
        "order_id",
        "payment_event_id",
        "fulfilment_event_id",
        "session_id",
        "account_move_id",
        "account_move_line_id",
        "bank_statement_line_id",
        "account_payment_id",
        "payment_transaction_id",
        "sale_order_id",
        "stock_picking_id",
        "stock_move_id",
        "attachment_id",
    )
    def _compute_link_key(self):
        fields_to_hash = (
            "link_type",
            "order_id",
            "payment_event_id",
            "fulfilment_event_id",
            "session_id",
            "account_move_id",
            "account_move_line_id",
            "bank_statement_line_id",
            "account_payment_id",
            "payment_transaction_id",
            "sale_order_id",
            "stock_picking_id",
            "stock_move_id",
            "attachment_id",
        )
        for link in self:
            values = []
            for field_name in fields_to_hash:
                value = link[field_name]
                values.append(str(value.id if hasattr(value, "id") else value or ""))
            link.link_key = hashlib.sha256(
                "\x1f".join(values).encode(),
                usedforsecurity=False,
            ).hexdigest()

    @api.constrains(
        "order_id",
        "payment_event_id",
        "fulfilment_event_id",
        "session_id",
        "account_move_id",
        "account_move_line_id",
        "bank_statement_line_id",
        "account_payment_id",
        "payment_transaction_id",
        "sale_order_id",
        "stock_picking_id",
        "stock_move_id",
        "attachment_id",
    )
    def _check_subject_and_target(self):
        for link in self:
            if not any(
                (link.order_id, link.payment_event_id, link.fulfilment_event_id, link.session_id),
            ):
                raise ValidationError(
                    self.env._("An accounting link requires a B2C business record."),
                )
            if not any(
                (
                    link.account_move_id,
                    link.account_move_line_id,
                    link.bank_statement_line_id,
                    link.account_payment_id,
                    link.payment_transaction_id,
                    link.sale_order_id,
                    link.stock_picking_id,
                    link.stock_move_id,
                    link.attachment_id,
                ),
            ):
                raise ValidationError(
                    self.env._("An accounting link requires a native or attachment target."),
                )
            if (
                link.account_move_id
                and link.account_move_line_id
                and link.account_move_line_id.move_id != link.account_move_id
            ):
                raise ValidationError(
                    self.env._("The accounting line does not belong to the selected move."),
                )

    def action_verify(self):
        self.write(
            {
                "link_state": "verified",
                "reviewed_by_id": self.env.user.id,
                "reviewed_at": fields.Datetime.now(),
            },
        )

    def action_reject(self):
        self.write(
            {
                "link_state": "rejected",
                "reviewed_by_id": self.env.user.id,
                "reviewed_at": fields.Datetime.now(),
            },
        )

    def _check_locked_session(self):
        if self.session_id.filtered(lambda session: session.state == "locked"):
            raise UserError(self.env._("Links in a locked B2C session are immutable."))

    @api.model_create_multi
    def create(self, vals_list):
        session_ids = {
            vals["session_id"]
            for vals in vals_list
            if vals.get("session_id")
        }
        if session_ids and self.env["b2c.accounting.session"].browse(
            session_ids,
        ).filtered(lambda session: session.state == "locked"):
            raise UserError(
                self.env._("Links cannot be added to a locked B2C session."),
            )
        return super().create(vals_list)

    def write(self, vals):
        self._check_locked_session()
        if vals.get("session_id"):
            target_session = self.env["b2c.accounting.session"].browse(
                vals["session_id"],
            )
            if target_session.state == "locked":
                raise UserError(
                    self.env._("Links cannot be moved into a locked B2C session."),
                )
        return super().write(vals)

    def unlink(self):
        self._check_locked_session()
        return super().unlink()
