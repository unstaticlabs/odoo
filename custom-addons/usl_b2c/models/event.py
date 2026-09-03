from odoo import api, fields, models
from odoo.exceptions import ValidationError

from .constants import (
    COMPLETENESS_STATES,
    CONVERSION_STATES,
    FULFILMENT_MODES,
    MAPPING_STATES,
    ORIGINS,
    REVIEW_STATES,
    SOURCE_PROVIDERS,
)


class B2cPaymentEvent(models.Model):
    _name = "b2c.payment.event"
    _description = "B2C Payment, Refund and Fee Event"
    _order = "event_date desc, id desc"
    _check_company_auto = True

    name = fields.Char(required=True)
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
    )
    order_id = fields.Many2one(
        "b2c.order",
        check_company=True,
        index=True,
        ondelete="restrict",
    )
    sale_order_id = fields.Many2one(
        related="order_id.sale_order_id",
        string="Native Sale Order",
        store=True,
        readonly=True,
    )
    source_provider = fields.Selection(SOURCE_PROVIDERS, required=True, index=True)
    origin = fields.Selection(ORIGINS, required=True, default="manual", index=True)
    event_type = fields.Selection(
        [
            ("payment", "Payment"),
            ("refund", "Refund"),
            ("fee", "Fee"),
            ("tax", "Tax"),
            ("deposit", "Deposit"),
            ("payout", "Payout"),
            ("chargeback", "Chargeback"),
            ("adjustment", "Adjustment"),
        ],
        required=True,
        index=True,
    )
    provider_event_key = fields.Char(required=True, index=True, copy=False, readonly=True)
    external_transaction_id = fields.Char(index=True, copy=False)
    external_order_id = fields.Char(index=True, copy=False)
    external_payment_intent_id = fields.Char(index=True, copy=False)
    external_session_id = fields.Char(index=True, copy=False)
    external_checkout_session_id = fields.Char(index=True, copy=False)
    external_payout_id = fields.Char(index=True, copy=False)
    external_refund_id = fields.Char(index=True, copy=False)
    external_original_payment_id = fields.Char(index=True, copy=False)
    original_event_id = fields.Many2one(
        "b2c.payment.event",
        string="Original Payment Event",
        check_company=True,
        ondelete="restrict",
        index=True,
    )
    original_provider_state = fields.Char(copy=False)
    state = fields.Selection(
        [
            ("pending", "Pending"),
            ("authorized", "Authorized"),
            ("captured", "Captured"),
            ("settled", "Settled"),
            ("failed", "Failed"),
            ("refunded", "Refunded"),
            ("cancelled", "Cancelled"),
            ("unknown", "Unknown"),
        ],
        required=True,
        default="unknown",
        index=True,
    )
    event_date = fields.Datetime(required=True, index=True)
    currency_id = fields.Many2one(
        "res.currency",
        string="Transaction Currency",
        required=True,
        ondelete="restrict",
        index=True,
    )
    company_currency_id = fields.Many2one(
        related="company_id.currency_id",
        string="Company Currency",
        store=True,
    )
    amount = fields.Monetary(currency_field="currency_id")
    fee_amount = fields.Monetary(currency_field="currency_id")
    tax_amount = fields.Monetary(currency_field="currency_id")
    refund_amount = fields.Monetary(currency_field="currency_id")
    net_amount = fields.Monetary(currency_field="currency_id")
    company_amount = fields.Monetary(currency_field="company_currency_id")
    fee_company_amount = fields.Monetary(currency_field="company_currency_id")
    tax_company_amount = fields.Monetary(currency_field="company_currency_id")
    refund_company_amount = fields.Monetary(currency_field="company_currency_id")
    net_company_amount = fields.Monetary(currency_field="company_currency_id")
    conversion_state = fields.Selection(
        CONVERSION_STATES,
        required=True,
        default="pending",
        index=True,
    )
    evidenced_conversion_rate = fields.Float(digits=(16, 8))
    conversion_evidence = fields.Char(copy=False)
    completeness_state = fields.Selection(
        COMPLETENESS_STATES,
        required=True,
        default="unknown",
        index=True,
    )
    mapping_state = fields.Selection(
        MAPPING_STATES,
        required=True,
        default="pending",
        index=True,
    )
    review_state = fields.Selection(
        REVIEW_STATES,
        required=True,
        default="pending",
        index=True,
    )
    order_link_state = fields.Selection(
        MAPPING_STATES,
        required=True,
        default="pending",
        index=True,
    )
    accounting_link_state = fields.Selection(
        MAPPING_STATES,
        required=True,
        default="pending",
        index=True,
    )
    accounting_link_note = fields.Char(
        copy=False,
        help=(
            "Explains whether accounting is linked directly, covered by an "
            "aggregate session, not applicable, or still unresolved."
        ),
    )
    bank_link_state = fields.Selection(
        MAPPING_STATES,
        required=True,
        default="pending",
        index=True,
    )
    payment_transaction_id = fields.Many2one(
        "payment.transaction",
        string="Native Payment Transaction",
        check_company=True,
        ondelete="restrict",
        copy=False,
        index=True,
    )
    account_payment_id = fields.Many2one(
        "account.payment",
        string="Native Account Payment",
        check_company=True,
        ondelete="restrict",
        copy=False,
        index=True,
    )
    supporting_attachment_id = fields.Many2one(
        "ir.attachment",
        check_company=True,
        ondelete="restrict",
        copy=False,
    )
    evidence_id = fields.Many2one(
        "b2c.provider.evidence",
        check_company=True,
        ondelete="restrict",
        copy=False,
    )
    accounting_link_ids = fields.One2many(
        "b2c.accounting.link",
        "payment_event_id",
        string="Accounting and Bank Evidence",
    )

    _company_provider_event_key_unique = models.Constraint(
        "UNIQUE(company_id, source_provider, provider_event_key)",
        "A provider payment/refund/fee key must be unique per company.",
    )

    @api.constrains("event_type", "refund_amount", "refund_company_amount", "amount")
    def _check_refund_sign(self):
        for event in self:
            if event.refund_amount > 0 or event.refund_company_amount > 0:
                raise ValidationError(
                    self.env._("B2C refunds must be stored as negative amounts."),
                )
            if event.event_type == "refund" and event.amount > 0:
                raise ValidationError(
                    self.env._("A refund event amount must be zero or negative."),
                )


class B2cFulfilmentEvent(models.Model):
    _name = "b2c.fulfilment.event"
    _description = "B2C Fulfilment and COGS Event"
    _order = "event_date desc, id desc"
    _check_company_auto = True

    name = fields.Char(required=True)
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
    )
    order_id = fields.Many2one(
        "b2c.order",
        check_company=True,
        index=True,
        ondelete="restrict",
    )
    sale_order_id = fields.Many2one(
        related="order_id.sale_order_id",
        string="Native Sale Order",
        store=True,
        readonly=True,
    )
    source_provider = fields.Selection(SOURCE_PROVIDERS, required=True, index=True)
    origin = fields.Selection(ORIGINS, required=True, default="manual", index=True)
    provider_event_key = fields.Char(required=True, index=True, copy=False, readonly=True)
    external_order_id = fields.Char(index=True, copy=False)
    external_fulfilment_id = fields.Char(index=True, copy=False)
    external_printful_id = fields.Char(index=True, copy=False)
    original_provider_state = fields.Char(copy=False)
    state = fields.Selection(
        [
            ("pending", "Pending"),
            ("partially_fulfilled", "Partially fulfilled"),
            ("fulfilled", "Fulfilled"),
            ("refunded", "Refunded"),
            ("cancelled", "Cancelled"),
            ("unknown", "Unknown"),
        ],
        required=True,
        default="unknown",
        index=True,
    )
    fulfilment_mode = fields.Selection(
        FULFILMENT_MODES,
        required=True,
        default="unknown",
        index=True,
    )
    event_date = fields.Datetime(required=True, index=True)
    destination_country_id = fields.Many2one("res.country", ondelete="restrict", index=True)
    origin_country_codes = fields.Char(copy=False)
    currency_id = fields.Many2one(
        "res.currency",
        string="Transaction Currency",
        required=True,
        ondelete="restrict",
        index=True,
    )
    company_currency_id = fields.Many2one(
        related="company_id.currency_id",
        string="Company Currency",
        store=True,
    )
    product_cost_amount = fields.Monetary(currency_field="currency_id")
    discount_amount = fields.Monetary(currency_field="currency_id")
    shipping_cost_amount = fields.Monetary(currency_field="currency_id")
    digitalization_cost_amount = fields.Monetary(currency_field="currency_id")
    tax_amount = fields.Monetary(currency_field="currency_id")
    vat_amount = fields.Monetary(currency_field="currency_id")
    cogs_amount = fields.Monetary(currency_field="currency_id")
    company_cogs_amount = fields.Monetary(currency_field="company_currency_id")
    conversion_state = fields.Selection(
        CONVERSION_STATES,
        required=True,
        default="pending",
        index=True,
    )
    evidenced_conversion_rate = fields.Float(digits=(16, 8))
    conversion_evidence = fields.Char(copy=False)
    completeness_state = fields.Selection(
        COMPLETENESS_STATES,
        required=True,
        default="unknown",
        index=True,
    )
    review_state = fields.Selection(
        REVIEW_STATES,
        required=True,
        default="pending",
        index=True,
    )
    order_link_state = fields.Selection(
        MAPPING_STATES,
        required=True,
        default="pending",
        index=True,
    )
    accounting_link_state = fields.Selection(
        MAPPING_STATES,
        required=True,
        default="pending",
        index=True,
    )
    accounting_link_note = fields.Char(
        copy=False,
        help=(
            "Explains whether accounting is linked directly, covered by an "
            "aggregate session, not applicable, or still unresolved."
        ),
    )
    stock_picking_id = fields.Many2one(
        "stock.picking",
        string="Native Stock Picking",
        check_company=True,
        ondelete="restrict",
        copy=False,
        index=True,
    )
    stock_picking_ids = fields.Many2many(
        "stock.picking",
        "b2c_fulfilment_picking_rel",
        "fulfilment_id",
        "picking_id",
        string="Native Stock Pickings",
        readonly=True,
    )
    stock_move_id = fields.Many2one(
        "stock.move",
        string="Native Stock Move",
        check_company=True,
        ondelete="restrict",
        copy=False,
        index=True,
    )
    stock_move_ids = fields.Many2many(
        "stock.move",
        "b2c_fulfilment_stock_move_rel",
        "fulfilment_id",
        "stock_move_id",
        string="Native Stock Moves",
        readonly=True,
    )
    sale_order_line_ids = fields.Many2many(
        "sale.order.line",
        "b2c_fulfilment_sale_line_rel",
        "fulfilment_id",
        "sale_line_id",
        string="Native Sales Lines",
        readonly=True,
    )
    purchase_order_id = fields.Many2one(
        "purchase.order",
        string="Native Purchase Order",
        check_company=True,
        ondelete="restrict",
        copy=False,
        index=True,
    )
    supporting_attachment_id = fields.Many2one(
        "ir.attachment",
        check_company=True,
        ondelete="restrict",
        copy=False,
    )
    evidence_id = fields.Many2one(
        "b2c.provider.evidence",
        check_company=True,
        ondelete="restrict",
        copy=False,
    )
    accounting_link_ids = fields.One2many(
        "b2c.accounting.link",
        "fulfilment_event_id",
        string="Accounting Evidence",
    )

    _company_provider_event_key_unique = models.Constraint(
        "UNIQUE(company_id, source_provider, provider_event_key)",
        "A provider fulfilment key must be unique per company.",
    )

    @api.constrains("state", "cogs_amount", "company_cogs_amount")
    def _check_refund_sign(self):
        for event in self:
            if event.state == "refunded" and (
                event.cogs_amount > 0 or event.company_cogs_amount > 0
            ):
                raise ValidationError(
                    self.env._("Refunded fulfilment/COGS events must be negative."),
                )
