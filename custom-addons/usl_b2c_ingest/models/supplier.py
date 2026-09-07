"""Attaching Printful's fulfilment to the sale it fulfilled.

Printful states the cost of a sale and, where it shipped the whole order, what
the customer was charged to ship it.  Which sale each fulfilment belongs to is
usually stated outright; where it is not, it is proved from the recipient and
the date, never assumed.
"""

from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from odoo import Command, api, fields, models
from odoo.exceptions import UserError

from odoo.addons.usl_b2c_ingest.parsers import (
    LINE_GRAIN,
    ORDER_GRAIN,
    jsonable,
    printful,
)
from odoo.addons.usl_b2c_ingest.services.printful import (
    TOKEN_PARAMETER,
    PrintfulClient,
    PrintfulError,
)

#: How far a fulfilment may sit from the sale it fulfils before the recipient
#: alone stops being evidence of the same order.
MATCH_WINDOW = timedelta(days=10)


class B2cImportRowFulfilment(models.Model):
    _inherit = "b2c.import.row"

    fulfilment_of_row_id = fields.Many2one(
        "b2c.import.row",
        readonly=True,
        ondelete="set null",
        index=True,
        string="Fulfils",
        help="The order row this supplier fulfilment belongs to.",
    )
    fulfilment_evidence = fields.Char(readonly=True)


class B2cImportBatchFulfilment(models.Model):
    _inherit = "b2c.import.batch"

    fulfilment_count = fields.Integer(compute="_compute_fulfilment_count", store=True)
    supplier_sales_cost = fields.Monetary(
        compute="_compute_supplier_cost",
        currency_field="supplier_currency_id",
        help="What the supplier charged to fulfil sales in this drop.",
    )
    supplier_internal_cost = fields.Monetary(
        compute="_compute_supplier_cost",
        currency_field="supplier_currency_id",
        help="What the supplier charged for orders no sale in this drop matched. "
             "An order that fulfils no sale at all is marketing or prototyping, "
             "which is a cost and never revenue; check the untied fulfilments "
             "before reading this as that.",
    )
    supplier_currency_id = fields.Many2one(
        related="company_id.currency_id",
        string="Supplier currency",
    )

    @api.depends("row_ids.resolution", "row_ids.grain")
    def _compute_fulfilment_count(self):
        for batch in self:
            batch.fulfilment_count = len(batch._fulfilment_rows())

    @api.depends("row_ids.fulfilment_of_row_id", "row_ids.resolution")
    def _compute_supplier_cost(self):
        for batch in self:
            sales = internal = Decimal("0")
            for row in batch._fulfilment_rows():
                cost = Decimal(str((row.values or {}).get("costs_total") or "0"))
                if row.fulfilment_of_row_id:
                    sales += cost
                else:
                    internal += cost
            batch.supplier_sales_cost = float(sales)
            batch.supplier_internal_cost = float(internal)

    def _supplier_cost_by_month(self):
        """Return what the supplier charged each month, for sales and for neither.

        An order the supplier fulfilled that answers to no sale is marketing or
        prototyping: a cost of finding out what to sell, never the cost of
        something sold. Which month it falls in is what the wallet is settled by.
        """
        self.ensure_one()
        found = defaultdict(lambda: {"sales": Decimal("0"), "internal": Decimal("0")})
        for row in self._fulfilment_rows():
            if not row.occurred_at:
                continue
            cost = Decimal(str((row.values or {}).get("costs_total") or "0"))
            period = row.occurred_at.date().replace(day=1)
            found[period]["sales" if row.fulfilment_of_row_id else "internal"] += cost
        return dict(sorted(found.items()))

    def _fulfilment_rows(self):
        return self.row_ids.filtered(
            lambda row: row.resolution == "supplier" and row.grain == ORDER_GRAIN,
        )

    # -- reading the supplier ----------------------------------------------

    def _printful_client(self):
        token = self.env["ir.config_parameter"].sudo().get_str(TOKEN_PARAMETER)
        if not token:
            raise UserError(
                self.env._(
                    "No Printful token is configured. Set the system parameter "
                    "%(parameter)s to read fulfilment.",
                    parameter=TOKEN_PARAMETER,
                ),
            )
        return PrintfulClient(token)

    def _store_channels(self):
        """Return the channel each Printful store fulfils for."""
        self.ensure_one()
        channels = self.env["b2c.channel"].search(
            [
                ("company_id", "=", self.company_id.id),
                ("printful_store_id", "!=", False),
            ],
        )
        return {
            channel.printful_store_id: (channel.code, "sale")
            for channel in channels
        }

    def action_fetch_fulfilment(self):
        """Read Printful for the period this batch covers, and attach what it fulfilled."""
        for batch in self:
            if batch.state == "draft":
                raise UserError(batch.env._("Read the files before reading the supplier."))
            try:
                orders = list(batch._printful_client().orders())
            except PrintfulError as error:
                raise UserError(str(error)) from error
            batch._store_fulfilment(orders)
            batch.write({"report": batch._build_report()})
        return True

    def _store_fulfilment(self, orders):
        """Keep the fulfilments that fall in this batch's period, and link them."""
        self.ensure_one()
        self._fulfilment_rows().fulfilment_of_row_id = False
        self.row_ids.filtered(
            lambda row: row.format_id == "printful_orders",
        ).unlink()
        store_channels = self._store_channels()
        rows = list(printful.parse_orders(self._within_period(orders), store_channels))
        created = self.env["b2c.import.row"].create(
            [
                {
                    "batch_id": self.id,
                    "format_id": row.format_id,
                    "provider": row.provider,
                    "grain": row.grain,
                    "external_order_id": row.external_order_id,
                    "external_line_id": row.external_line_id or False,
                    "occurred_at": row.occurred_at,
                    "payload_digest": row.payload_digest,
                    "values": jsonable(row.values),
                    "payload": jsonable(row.payload),
                    "mapping": "not_applicable",
                    "resolution": "supplier",
                }
                for row in rows
            ],
        )
        self._link_fulfilment(created)
        self._record_known_fulfilment()
        self._report_unmatched_fulfilment(created)
        return created

    def _report_unmatched_fulfilment(self, fulfilments):
        """Report only the fulfilments that should have found a sale in this drop.

        A fulfilment older than the sales this drop is adding belongs to
        history Odoo already holds, and saying so every time would bury the
        ones that matter.
        """
        self.ensure_one()
        self.issue_ids.filtered(
            lambda issue: issue.kind == "fulfilment_unmatched",
        ).unlink()
        dates = [
            row.occurred_at
            for row in self.row_ids
            if row.resolution == "new" and row.grain == ORDER_GRAIN and row.occurred_at
        ]
        if not dates:
            return
        start, end = min(dates) - MATCH_WINDOW, max(dates) + MATCH_WINDOW
        stranded = fulfilments.filtered(
            lambda row: row.grain == ORDER_GRAIN
            and not row.fulfilment_of_row_id
            and row.occurred_at
            and start <= row.occurred_at <= end,
        )
        for row in stranded:
            self._raise_issue(
                "fulfilment_unmatched",
                self.env._(
                    "A fulfilment of %(date)s belongs to no sale in this drop",
                    date=row.occurred_at.date(),
                ),
                external_order_id=row.external_order_id,
                row=row,
                severity="advisory",
                note=self.env._(
                    "The supplier states %(reference)s, which no export in this drop "
                    "names, and its recipient matches no sale within %(days)s days.",
                    reference=row.external_order_id,
                    days=MATCH_WINDOW.days,
                ),
            )

    def _within_period(self, orders):
        """Return the fulfilments that could belong to the sales in this batch."""
        self.ensure_one()
        if not (self.period_start and self.period_end):
            return orders
        start = (self.period_start - MATCH_WINDOW).isoformat()
        end = (self.period_end + MATCH_WINDOW).isoformat()
        return [
            order
            for order in orders
            if start <= (order.get("created_at") or "")[:10] <= end
        ]

    def _link_fulfilment(self, fulfilments):
        """Say which sale each fulfilment belongs to, and how that is known."""
        self.ensure_one()
        sales = self.row_ids.filtered(
            lambda row: row.grain == ORDER_GRAIN and row.resolution != "supplier",
        )
        by_reference = {}
        by_email = {}
        for row in sales:
            by_reference[row.external_order_id] = row
            order = row.order_id
            if order.external_order_id:
                by_reference.setdefault(order.external_order_id, row)
            email = ((row.values or {}).get("customer_email") or "").strip().casefold()
            if email:
                by_email.setdefault(email, []).append(row)
        for fulfilment in fulfilments.filtered(lambda row: row.grain == ORDER_GRAIN):
            values = fulfilment.values or {}
            match = by_reference.get(fulfilment.external_order_id)
            evidence = self.env._("The supplier states the order reference.")
            if match is None:
                match, evidence = self._match_by_recipient(by_email, fulfilment, values)
            if match is None:
                continue
            fulfilment.write(
                {"fulfilment_of_row_id": match.id, "fulfilment_evidence": evidence},
            )

    def _match_by_recipient(self, by_email, fulfilment, values):
        """Return the sale a fulfilment belongs to when its reference is silent."""
        email = (values.get("customer_email") or "").strip().casefold()
        if not email or not fulfilment.occurred_at:
            return None, ""
        candidates = [
            row
            for row in by_email.get(email, ())
            if row.occurred_at
            and abs(row.occurred_at - fulfilment.occurred_at) <= MATCH_WINDOW
        ]
        if len(candidates) != 1:
            return None, ""
        return candidates[0], self.env._(
            "Same recipient and within %(days)s days of the sale.",
            days=MATCH_WINDOW.days,
        )

    # -- what a fulfilment costs -------------------------------------------

    def _record_fulfilment(self, fulfilment, order, channel):
        """Record what the supplier shipped for a sale, and what it charged.

        A B2C line's cost is decided in one place: the fulfilment event
        allocates what the supplier billed across the lines it shipped, pro
        rata by revenue, and carries the last shipped unit cost onto the
        product.  So this states the cost and which lines it covers and lets
        the allocation do the rest — writing a line's cost here instead would
        be a second opinion the next fulfilment would silently overrule.
        """
        self.ensure_one()
        # Carriage is what the customer paid to ship, not what shipping cost,
        # so the supplier's bill is never spread over it.
        goods = order.sale_order_id.order_line.filtered(
            lambda line: line.product_id != channel.shipping_product_id,
        )
        if not goods:
            return self.env["b2c.fulfilment.event"]
        values = fulfilment.values or {}
        key = f"printful:{values.get('printful_order_id') or fulfilment.external_order_id}"
        Event = self._trusted("b2c.fulfilment.event")
        found = Event.search(
            [
                ("company_id", "=", self.company_id.id),
                ("source_provider", "=", "printful"),
                ("provider_event_key", "=", key),
            ],
            limit=1,
        )
        stated = self._fulfilment_values(fulfilment, values, key, order, goods)
        if found:
            # A corrected or refunded fulfilment must still reach the lines,
            # and writing the cost and the links is what makes the allocation
            # run again.
            found.write(stated)
            return found
        return Event.create(stated)

    def _record_known_fulfilment(self):
        """Record every fulfilment whose sale exists, in this drop or already.

        A refund or a correction arrives long after the order it belongs to,
        in a drop that covers the refund rather than the sale. Matching it only
        against the orders in hand would leave the sale it actually changes
        costed as though nothing had happened.
        """
        self.ensure_one()
        channels = self._channels()
        for fulfilment in self._fulfilment_rows().filtered(
            lambda item: item.grain == ORDER_GRAIN,
        ):
            order = fulfilment.fulfilment_of_row_id.order_id or self._fulfilled_order(fulfilment)
            channel = channels.get(order.channel_id.code) or channels.get(fulfilment.provider)
            if order.sale_order_id and channel:
                self._record_fulfilment(fulfilment, order, channel)
        return True

    def _fulfilled_order(self, fulfilment):
        """Return the order a fulfilment names, whether or not it is in this drop."""
        self.ensure_one()
        reference = fulfilment.external_order_id
        if not reference:
            return self.env["b2c.order"]
        return self.env["b2c.order"].search(
            [
                ("company_id", "=", self.company_id.id),
                ("sale_order_id", "!=", False),
                ("superseded_by_id", "=", False),
                "|",
                ("external_order_id", "=", reference),
                ("external_display_id", "=", reference),
            ],
            limit=1,
        )

    def _fulfilment_values(self, fulfilment, values, key, order, goods):
        """State one Printful order as the fulfilment event it is."""
        self.ensure_one()
        refunded = (values.get("original_provider_state") or "").casefold() == "refunded"
        # A refund gives back what was charged, so it costs the negative of it.
        sign = Decimal("-1") if refunded else Decimal("1")
        billed = sign * self._cost(values, "costs_total")
        currency = self._fulfilment_currency(values)
        company_billed = billed
        if currency and currency != self.company_id.currency_id:
            company_billed = Decimal(
                str(
                    currency._convert(
                        float(billed),
                        self.company_id.currency_id,
                        self.company_id,
                        fulfilment.occurred_at or fields.Datetime.now(),
                    ),
                ),
            )
        return {
            "name": self.env._(
                "Printful %(reference)s",
                reference=values.get("printful_order_id") or fulfilment.external_order_id,
            ),
            "company_id": self.company_id.id,
            "channel_id": order.channel_id.id or False,
            "order_id": order.id or False,
            "source_provider": "printful",
            "origin": "imported",
            "provider_event_key": key,
            "external_order_id": fulfilment.external_order_id,
            "external_printful_id": values.get("printful_order_id") or False,
            "original_provider_state": values.get("original_provider_state") or False,
            "state": "refunded" if refunded else "fulfilled",
            "fulfilment_mode": "printful",
            "event_date": fulfilment.occurred_at,
            "destination_country_id": self._country(values).id or False,
            "currency_id": (currency or self.company_id.currency_id).id,
            "product_cost_amount": float(sign * self._cost(values, "costs_subtotal")),
            "discount_amount": float(sign * self._cost(values, "costs_discount")),
            "shipping_cost_amount": float(sign * self._cost(values, "costs_shipping")),
            "digitalization_cost_amount": float(sign * self._cost(values, "costs_digitization")),
            "tax_amount": float(sign * self._cost(values, "costs_tax")),
            "vat_amount": float(sign * self._cost(values, "costs_vat")),
            "cogs_amount": float(billed),
            "company_cogs_amount": float(company_billed),
            "conversion_state": (
                "not_needed"
                if not currency or currency == self.company_id.currency_id
                else "evidenced"
            ),
            "completeness_state": "complete",
            "review_state": "pending",
            "order_link_state": "verified",
            "accounting_link_state": "pending",
            "evidence_id": self._evidence(fulfilment).id,
            # Which lines it shipped is what the allocation spreads the cost
            # over, and it is provenance, so it is written with the token.
            "sale_order_line_ids": [Command.set(goods.ids)],
        }

    def _fulfilment_currency(self, values):
        """Return the currency the supplier billed in, if it names one."""
        self.ensure_one()
        code = (values.get("costs_currency") or "").strip().upper()
        if not code:
            return self.env["res.currency"]
        return self.env["res.currency"].with_context(active_test=False).search(
            [("name", "=", code)], limit=1,
        )

    @staticmethod
    def _cost(values, field):
        return Decimal(str(values.get(field) or "0"))

    # -- what a fulfilment proves ------------------------------------------

    def _supplier_costs(self):
        """Return the supplier cost of each mapped line, by import row."""
        self.ensure_one()
        costs = {}
        for fulfilment in self._fulfilment_rows().filtered("fulfilment_of_row_id"):
            sale_row = fulfilment.fulfilment_of_row_id
            lines = self.row_ids.filtered(
                lambda row, parent=fulfilment: row.resolution == "supplier"
                and row.grain == LINE_GRAIN
                and row.external_order_id == parent.external_order_id,
            )
            for line in lines:
                values = line.values or {}
                costs.setdefault(sale_row.id, []).append(
                    {
                        "name": values.get("original_name") or "",
                        "sku": values.get("original_sku") or "",
                        "quantity": Decimal(str(values.get("quantity") or "0")),
                        "unit_cost": Decimal(str(values.get("supplier_unit_cost") or "0")),
                        "unit_price": Decimal(str(values.get("unit_price") or "0")),
                    },
                )
        return costs

    def _evidenced_shipping(self, sale_row, sold_total):
        """Return the shipping a full fulfilment proves the customer was charged.

        A fulfilment that covers only part of an order says nothing about what
        the whole order was charged to ship, so it is not used for that.
        """
        self.ensure_one()
        for fulfilment in self._fulfilment_rows():
            if fulfilment.fulfilment_of_row_id != sale_row:
                continue
            values = fulfilment.values or {}
            retail = Decimal(str(values.get("retail_costs_subtotal") or "0"))
            if retail and retail == sold_total:
                return Decimal(str(values.get("retail_costs_shipping") or "0"))
        return None
