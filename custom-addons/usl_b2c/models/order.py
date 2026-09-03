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


class B2cOrder(models.Model):
    _name = "b2c.order"
    _description = "Canonical B2C Order"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "order_date desc, id desc"
    _check_company_auto = True

    name = fields.Char(required=True, index=True, tracking=True)
    canonical_key = fields.Char(required=True, index=True, copy=False, readonly=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
        ondelete="cascade",
    )
    channel_id = fields.Many2one(
        "b2c.channel",
        required=True,
        check_company=True,
        index=True,
        ondelete="restrict",
        tracking=True,
    )
    source_provider = fields.Selection(
        SOURCE_PROVIDERS,
        required=True,
        index=True,
        tracking=True,
    )
    origin = fields.Selection(ORIGINS, required=True, default="manual", index=True)
    external_order_id = fields.Char(index=True, copy=False)
    external_display_id = fields.Char(index=True, copy=False)
    original_provider_state = fields.Char(copy=False)
    source_payment_state = fields.Char(copy=False, readonly=True)
    source_fulfilment_state = fields.Char(copy=False, readonly=True)
    state = fields.Selection(
        [
            ("pending", "Pending"),
            ("confirmed", "Confirmed"),
            ("partially_fulfilled", "Partially fulfilled"),
            ("fulfilled", "Fulfilled"),
            ("partially_refunded", "Partially refunded"),
            ("refunded", "Refunded"),
            ("cancelled", "Cancelled"),
            ("unknown", "Unknown"),
        ],
        required=True,
        default="unknown",
        index=True,
        tracking=True,
    )
    order_date = fields.Datetime(required=True, index=True)
    payment_date = fields.Datetime(index=True)
    refund_date = fields.Datetime(index=True)
    fulfilment_date = fields.Datetime(index=True)
    customer_external_id = fields.Char(index=True, copy=False, readonly=True)
    customer_name = fields.Char(copy=False, readonly=True)
    customer_email = fields.Char(copy=False, readonly=True)
    shipping_name = fields.Char(copy=False, readonly=True)
    shipping_street = fields.Char(copy=False, readonly=True)
    shipping_street2 = fields.Char(copy=False, readonly=True)
    shipping_city = fields.Char(copy=False, readonly=True)
    shipping_state = fields.Char(copy=False, readonly=True)
    shipping_zip = fields.Char(copy=False, readonly=True)
    shipping_address_raw = fields.Text(copy=False, readonly=True)
    country_id = fields.Many2one("res.country", ondelete="restrict", index=True)
    original_country = fields.Char(copy=False, index=True)
    currency_id = fields.Many2one(
        "res.currency",
        string="Transaction Currency",
        ondelete="restrict",
        index=True,
    )
    company_currency_id = fields.Many2one(
        related="company_id.currency_id",
        string="Company Currency",
        store=True,
    )

    subtotal_amount = fields.Monetary(currency_field="currency_id")
    shipping_amount = fields.Monetary(currency_field="currency_id")
    discount_amount = fields.Monetary(currency_field="currency_id")
    tax_amount = fields.Monetary(currency_field="currency_id")
    fee_amount = fields.Monetary(currency_field="currency_id")
    refund_amount = fields.Monetary(currency_field="currency_id")
    revenue_amount = fields.Monetary(currency_field="currency_id")
    total_amount = fields.Monetary(currency_field="currency_id")
    net_amount = fields.Monetary(currency_field="currency_id")

    subtotal_company_amount = fields.Monetary(currency_field="company_currency_id")
    shipping_company_amount = fields.Monetary(currency_field="company_currency_id")
    discount_company_amount = fields.Monetary(currency_field="company_currency_id")
    tax_company_amount = fields.Monetary(currency_field="company_currency_id")
    fee_company_amount = fields.Monetary(currency_field="company_currency_id")
    refund_company_amount = fields.Monetary(currency_field="company_currency_id")
    revenue_company_amount = fields.Monetary(currency_field="company_currency_id")
    total_company_amount = fields.Monetary(currency_field="company_currency_id")
    net_company_amount = fields.Monetary(currency_field="company_currency_id")

    conversion_state = fields.Selection(
        CONVERSION_STATES,
        required=True,
        default="pending",
        index=True,
    )
    evidenced_conversion_rate = fields.Float(
        digits=(16, 8),
        help="Company-currency units evidenced for one transaction-currency unit.",
    )
    conversion_evidence = fields.Char(copy=False)
    amount_completeness = fields.Selection(
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
        tracking=True,
    )
    review_state = fields.Selection(
        REVIEW_STATES,
        required=True,
        default="pending",
        index=True,
        tracking=True,
    )
    fulfilment_mode = fields.Selection(
        FULFILMENT_MODES,
        required=True,
        default="unknown",
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
    payment_link_state = fields.Selection(
        MAPPING_STATES,
        required=True,
        default="pending",
        index=True,
    )
    fulfilment_link_state = fields.Selection(
        MAPPING_STATES,
        required=True,
        default="pending",
        index=True,
    )
    document_link_state = fields.Selection(
        MAPPING_STATES,
        required=True,
        default="pending",
        index=True,
    )
    sale_order_id = fields.Many2one(
        "sale.order",
        string="Native Sale Order",
        check_company=True,
        ondelete="restrict",
        copy=False,
        index=True,
        help="Native Sales record promoted from this immutable source order.",
    )
    partner_identity_id = fields.Many2one(
        "b2c.partner.identity",
        string="Provider Contact Identity",
        check_company=True,
        ondelete="restrict",
        copy=False,
        index=True,
        readonly=True,
    )
    partner_id = fields.Many2one(
        "res.partner",
        string="Native Customer",
        check_company=True,
        ondelete="restrict",
        copy=False,
        readonly=True,
    )
    shipping_partner_id = fields.Many2one(
        "res.partner",
        string="Native Delivery Contact",
        check_company=True,
        ondelete="restrict",
        copy=False,
        readonly=True,
    )
    supporting_attachment_id = fields.Many2one(
        "ir.attachment",
        string="Supporting Attachment",
        check_company=True,
        ondelete="restrict",
        copy=False,
    )
    notes = fields.Text()

    line_ids = fields.One2many("b2c.order.line", "order_id", string="Order Lines")
    source_record_ids = fields.One2many(
        "b2c.order.source",
        "order_id",
        string="Source Evidence",
    )
    payment_event_ids = fields.One2many(
        "b2c.payment.event",
        "order_id",
        string="Payment and Refund Events",
    )
    fulfilment_event_ids = fields.One2many(
        "b2c.fulfilment.event",
        "order_id",
        string="Fulfilment Events",
    )
    accounting_link_ids = fields.One2many(
        "b2c.accounting.link",
        "order_id",
        string="Accounting and Bank Evidence",
    )
    line_revenue_company_amount = fields.Monetary(
        currency_field="company_currency_id",
        compute="_compute_line_coverage",
        store=True,
    )
    unallocated_revenue_company_amount = fields.Monetary(
        currency_field="company_currency_id",
        compute="_compute_line_coverage",
        store=True,
    )
    line_revenue_coverage_percent = fields.Float(
        string="Line Revenue Coverage (%)",
        compute="_compute_line_coverage",
        store=True,
        aggregator="avg",
    )

    _company_canonical_key_unique = models.Constraint(
        "UNIQUE(company_id, canonical_key)",
        "A canonical B2C order key must be unique per company.",
    )

    @api.depends("line_ids.revenue_company_amount", "revenue_company_amount")
    def _compute_line_coverage(self):
        for order in self:
            allocated = sum(order.line_ids.mapped("revenue_company_amount"))
            order.line_revenue_company_amount = allocated
            order.unallocated_revenue_company_amount = (
                order.revenue_company_amount - allocated
            )
            denominator = abs(order.revenue_company_amount)
            order.line_revenue_coverage_percent = (
                min(100.0, abs(allocated) / denominator * 100.0)
                if denominator
                else 0.0
            )

    @api.constrains("refund_amount", "refund_company_amount")
    def _check_refund_sign(self):
        for order in self:
            if order.refund_amount > 0 or order.refund_company_amount > 0:
                raise ValidationError(
                    self.env._("B2C refunds must be stored as negative amounts."),
                )

    def action_open_native_sale_order(self):
        self.ensure_one()
        if not self.sale_order_id:
            return False
        return {
            "type": "ir.actions.act_window",
            "name": self.sale_order_id.display_name,
            "res_model": "sale.order",
            "res_id": self.sale_order_id.id,
            "view_mode": "form",
        }


class B2cOrderLine(models.Model):
    _name = "b2c.order.line"
    _description = "B2C Order Line"
    _order = "order_id, sequence, id"
    _check_company_auto = True

    order_id = fields.Many2one(
        "b2c.order",
        required=True,
        ondelete="cascade",
        index=True,
    )
    line_key = fields.Char(required=True, readonly=True, copy=False, index=True)
    company_id = fields.Many2one(related="order_id.company_id", store=True, index=True)
    channel_id = fields.Many2one(related="order_id.channel_id", store=True, index=True)
    source_provider = fields.Selection(
        related="order_id.source_provider",
        store=True,
        index=True,
    )
    order_date = fields.Datetime(related="order_id.order_date", store=True, index=True)
    country_id = fields.Many2one(related="order_id.country_id", store=True, index=True)
    sequence = fields.Integer(default=10)
    external_line_id = fields.Char(index=True, copy=False)
    external_transaction_id = fields.Char(index=True, copy=False)
    external_listing_id = fields.Char(index=True, copy=False)
    original_sku = fields.Char(index=True, copy=False)
    original_name = fields.Char(required=True, copy=False)
    original_variation = fields.Text(copy=False)
    quantity = fields.Float(required=True, digits="Product Unit", default=1.0)
    currency_id = fields.Many2one(related="order_id.currency_id", store=True)
    company_currency_id = fields.Many2one(
        related="order_id.company_currency_id",
        store=True,
    )
    unit_price = fields.Monetary(currency_field="currency_id")
    subtotal_amount = fields.Monetary(currency_field="currency_id")
    discount_amount = fields.Monetary(currency_field="currency_id")
    shipping_amount = fields.Monetary(currency_field="currency_id")
    tax_amount = fields.Monetary(currency_field="currency_id")
    revenue_amount = fields.Monetary(currency_field="currency_id")
    subtotal_company_amount = fields.Monetary(currency_field="company_currency_id")
    discount_company_amount = fields.Monetary(currency_field="company_currency_id")
    shipping_company_amount = fields.Monetary(currency_field="company_currency_id")
    tax_company_amount = fields.Monetary(currency_field="company_currency_id")
    revenue_company_amount = fields.Monetary(currency_field="company_currency_id")
    product_id = fields.Many2one(
        "product.product",
        check_company=True,
        ondelete="restrict",
        index=True,
    )
    alias_id = fields.Many2one(
        "b2c.product.alias",
        check_company=True,
        ondelete="restrict",
        index=True,
    )
    mapping_state = fields.Selection(
        MAPPING_STATES,
        required=True,
        default="pending",
        index=True,
    )
    amount_completeness = fields.Selection(
        COMPLETENESS_STATES,
        required=True,
        default="unknown",
        index=True,
    )
    evidence_id = fields.Many2one(
        "b2c.provider.evidence",
        check_company=True,
        ondelete="restrict",
        copy=False,
    )
    sale_order_line_id = fields.Many2one(
        "sale.order.line",
        string="Native Sales Line",
        check_company=True,
        ondelete="restrict",
        copy=False,
        index=True,
        readonly=True,
    )
    production_ids = fields.Many2many(
        "mrp.production",
        "b2c_order_line_production_rel",
        "b2c_line_id",
        "production_id",
        string="Manufacturing Orders",
        readonly=True,
    )
    stock_move_ids = fields.Many2many(
        "stock.move",
        "b2c_order_line_stock_move_rel",
        "b2c_line_id",
        "stock_move_id",
        string="Stock Movements",
        readonly=True,
    )

    _order_line_key_unique = models.Constraint(
        "UNIQUE(order_id, line_key)",
        "A source order line can be recorded only once on a canonical order.",
    )

    @api.constrains("mapping_state", "product_id", "alias_id")
    def _check_verified_mapping(self):
        for line in self:
            if line.mapping_state == "verified" and not line.product_id:
                raise ValidationError(
                    self.env._("A verified B2C line mapping requires a product."),
                )
            if line.alias_id and line.alias_id.product_id != line.product_id:
                raise ValidationError(
                    self.env._("The line product must match the verified SKU alias."),
                )


class B2cOrderSource(models.Model):
    _name = "b2c.order.source"
    _description = "B2C Order Source Evidence"
    _order = "order_id, source_precedence desc, id"
    _check_company_auto = True

    order_id = fields.Many2one(
        "b2c.order",
        required=True,
        ondelete="cascade",
        index=True,
    )
    company_id = fields.Many2one(related="order_id.company_id", store=True, index=True)
    channel_id = fields.Many2one(related="order_id.channel_id", store=True, index=True)
    source_provider = fields.Selection(SOURCE_PROVIDERS, required=True, index=True)
    origin = fields.Selection(ORIGINS, required=True, default="imported")
    source_record_key = fields.Char(required=True, index=True, copy=False)
    external_order_id = fields.Char(index=True, copy=False)
    external_display_id = fields.Char(index=True, copy=False)
    external_transaction_id = fields.Char(index=True, copy=False)
    external_payment_intent_id = fields.Char(index=True, copy=False)
    external_session_id = fields.Char(index=True, copy=False)
    external_checkout_session_id = fields.Char(index=True, copy=False)
    external_payout_id = fields.Char(index=True, copy=False)
    external_refund_id = fields.Char(index=True, copy=False)
    external_fulfilment_id = fields.Char(index=True, copy=False)
    external_printful_id = fields.Char(index=True, copy=False)
    external_listing_id = fields.Char(index=True, copy=False)
    original_provider_state = fields.Char(copy=False)
    source_precedence = fields.Integer(required=True, default=10)
    is_primary = fields.Boolean(default=False)
    completeness_state = fields.Selection(
        COMPLETENESS_STATES,
        required=True,
        default="unknown",
    )
    provider_payload_digest = fields.Char(required=True, copy=False, index=True)
    evidence_id = fields.Many2one(
        "b2c.provider.evidence",
        required=True,
        check_company=True,
        ondelete="restrict",
        copy=False,
    )

    _company_source_key_unique = models.Constraint(
        "UNIQUE(company_id, source_provider, source_record_key)",
        "A provider order source key must be unique per company.",
    )

    @api.constrains("is_primary", "order_id")
    def _check_one_primary_source(self):
        for source in self.filtered("is_primary"):
            if self.search_count(
                [("order_id", "=", source.order_id.id), ("is_primary", "=", True)],
                limit=2,
            ) > 1:
                raise ValidationError(
                    self.env._("A canonical B2C order can have only one primary source."),
                )
