"""Turning a resolved drop into commerce, sales and stock.

Everything before this point only reads.  Applying a batch is where Odoo gains
the sale: a contact, the canonical order the channels agree on, a native sales
order carrying the tax its destination is owed, the delivery its own-stock
lines need, and the cost the supplier charged.  It refuses to run while
anything is unresolved, and running it twice adds nothing.
"""

from decimal import Decimal

from odoo import Command, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare

from odoo.addons.usl_b2c.models.native_history import (
    MATERIALIZATION_CONTEXT,
    MATERIALIZATION_TOKEN,
)

#: A drop states money to the cent; anything coarser is a defect, not rounding.
CENT = Decimal("0.01")


class B2cImportBatchMaterialise(models.Model):
    _inherit = "b2c.import.batch"

    applied_order_count = fields.Integer(readonly=True, copy=False)
    applied_sale_ids = fields.Many2many("sale.order", string="Sales created", copy=False)

    # -- applying ----------------------------------------------------------

    def action_apply(self):
        """Create the commerce, the sales and the stock this drop describes."""
        for batch in self:
            batch._assert_ready()
            created = batch.env["sale.order"]
            for row in batch._orders_to_create():
                created |= batch._materialise_order(row)
            batch.write(
                {
                    "state": "applied",
                    "applied_order_count": len(created),
                    "applied_sale_ids": [Command.set(created.ids)],
                },
            )
            batch.write({"report": batch._build_report()})
        return True

    def action_open_sales(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Sales from this import"),
            "res_model": "sale.order",
            "view_mode": "list,form",
            "domain": [("id", "in", self.applied_sale_ids.ids)],
        }

    def _assert_ready(self):
        """Refuse to build on anything a person has not settled."""
        self.ensure_one()
        if self.state not in ("resolved", "applied"):
            raise UserError(self.env._("Resolve the products before applying the drop."))
        blocking = self.issue_ids.filtered(lambda issue: issue.severity == "blocking")
        if blocking:
            raise UserError(
                self.env._(
                    "Settle what is blocking first:\n%(findings)s",
                    findings="\n".join(f"- {issue.name}" for issue in blocking),
                ),
            )
        if self.unmapped_line_count:
            raise UserError(
                self.env._(
                    "%(count)s line(s) still have no product.",
                    count=self.unmapped_line_count,
                ),
            )
        self._assert_channels_can_sell()

    def _assert_channels_can_sell(self):
        """Refuse until each channel in the drop can carry what it charges."""
        self.ensure_one()
        channels = self._channels()
        missing = [
            channels[provider]
            for provider in {
                row.provider
                for row in self._orders_to_create()
                if self._order_money(row)["shipping"]
            }
            if provider in channels and not channels[provider].shipping_product_id
        ]
        if missing:
            raise UserError(
                self.env._(
                    "These channels charge carriage but name no product for it: "
                    "%(channels)s",
                    channels=", ".join(channel.display_name for channel in missing),
                ),
            )

    def _orders_to_create(self):
        self.ensure_one()
        return self.row_ids.filtered(
            lambda row: row.grain == "order" and row.resolution == "new",
        ).sorted(lambda row: (row.occurred_at or fields.Datetime.now(), row.id))

    def _context(self):
        """Return the context the delivered module accepts provenance under."""
        return {MATERIALIZATION_CONTEXT: MATERIALIZATION_TOKEN}

    def _trusted(self, model):
        return self.env[model].sudo().with_context(**self._context())

    # -- money -------------------------------------------------------------

    def _lines_of(self, row):
        self.ensure_one()
        return self.row_ids.filtered(
            lambda line, order=row: line.grain == "line"
            and line.resolution != "supplier"
            and line.provider == order.provider
            and line.external_order_id == order.external_order_id,
        ).sorted("id")

    @staticmethod
    def _decimal(values, name):
        return Decimal(str((values or {}).get(name) or "0"))

    def _order_money(self, row):
        """Return what the customer paid, from the export and the supplier.

        A channel that exports its order money is believed.  One that does not
        is read from its lines, and the carriage is taken from a supplier
        fulfilment only when that fulfilment covered the whole order.
        """
        self.ensure_one()
        values = row.values or {}
        sold = sum(
            (self._decimal(line.values, "subtotal_amount") for line in self._lines_of(row)),
            Decimal("0"),
        )
        if "total_amount" in values:
            return {
                "sold": self._decimal(values, "subtotal_amount") or sold,
                "shipping": self._decimal(values, "shipping_amount"),
                "discount": self._decimal(values, "discount_amount"),
                "total": self._decimal(values, "total_amount"),
                "fee": self._decimal(values, "fee_amount"),
                "shipping_evidence": self.env._("The channel exports it."),
            }
        shipping = self._evidenced_shipping(row, sold)
        return {
            "sold": sold,
            "shipping": shipping or Decimal("0"),
            "discount": Decimal("0"),
            "total": sold + (shipping or Decimal("0")),
            "fee": Decimal("0"),
            "shipping_evidence": (
                self.env._("The supplier shipped the whole order.")
                if shipping is not None
                else self.env._("No evidence of carriage; none is recorded.")
            ),
        }

    @staticmethod
    def _discount_percent(money):
        """Return the per-line discount a channel's order-level discount means."""
        if not money["discount"] or not money["sold"]:
            return Decimal("0")
        return (money["discount"] / money["sold"] * Decimal("100")).quantize(CENT)

    # -- contacts ----------------------------------------------------------

    def _partner_for(self, row):
        """Return the contact this sale is for, created once per channel customer.

        The destination country is what decides the tax, so a sale is never
        created for a contact without one.
        """
        self.ensure_one()
        values = row.values or {}
        provider = row.provider
        external = (values.get("customer_external_id") or "").strip()
        identity_key = external or (values.get("customer_email") or "").strip() or (
            f"{provider}:{row.external_order_id}"
        )
        Identity = self._trusted("b2c.partner.identity")
        digest = f"{provider}:{identity_key}"
        found = Identity.search(
            [
                ("company_id", "=", self.company_id.id),
                ("source_provider", "=", provider),
                ("identity_digest", "=", digest),
                ("identity_role", "=", "customer"),
            ],
            limit=1,
        )
        if found:
            return found.partner_id
        country = self._country(values)
        if not country:
            raise UserError(
                self.env._(
                    "Order %(order)s names no country, so its tax cannot be settled.",
                    order=row.external_order_id,
                ),
            )
        partner = self._trusted("res.partner").create(
            {
                "name": (values.get("shipping_name") or values.get("customer_name")
                         or self.env._("Customer of %(order)s", order=row.external_order_id)),
                "company_id": False,
                "type": "contact",
                "street": values.get("shipping_street") or False,
                "street2": values.get("shipping_street2") or False,
                "city": values.get("shipping_city") or False,
                "zip": values.get("shipping_zip") or False,
                "country_id": country.id,
                "email": (values.get("customer_email") or "") or False,
                "customer_rank": 1,
            },
        )
        Identity.create(
            {
                "company_id": self.company_id.id,
                "source_provider": provider,
                "identity_role": "customer",
                "identity_digest": digest,
                "external_customer_id": external or False,
                "name": partner.name,
                "partner_id": partner.id,
            },
        )
        return partner

    # -- tax ---------------------------------------------------------------

    def _assert_destination_tax(self, sale):
        """Refuse a sale taxed at a rate its destination is not owed.

        The rule is stated rather than the configuration trusted: a sale is
        taxed by the country it ships to, at home or in another Member State,
        and not at all outside the Union.  Which position answered is what
        proves it, because the tax records themselves all belong to the
        company's own country whatever rate they carry.  A generic position
        shadowing the one built for a destination is a silent way to invoice
        the wrong VAT, and that is what this refuses.
        """
        self.ensure_one()
        country = sale.partner_shipping_id.country_id
        home = self.company_id.account_fiscal_country_id
        in_union = country in self.env.ref("base.europe").country_ids
        position = sale.fiscal_position_id
        taxes = sale.order_line.tax_ids
        charged = taxes.filtered("amount")
        if country == home:
            expected = bool(charged) and (position.is_domestic or position.country_id == home)
        elif in_union:
            expected = bool(charged) and position.country_id == country
        else:
            expected = not charged
        if not expected:
            raise UserError(
                self.env._(
                    "%(order)s ships to %(country)s but is taxed as %(taxes)s under "
                    "%(position)s. Correct the fiscal positions before applying.",
                    order=sale.client_order_ref,
                    country=country.display_name,
                    taxes=", ".join(taxes.mapped("name")) or self.env._("no tax"),
                    position=sale.fiscal_position_id.display_name
                    or self.env._("no fiscal position"),
                ),
            )
        self._assert_tax_included(charged)

    def _assert_tax_included(self, taxes):
        """Refuse a tax that would be added to what the customer already paid."""
        self.ensure_one()
        added = taxes.filtered(
            lambda tax: tax.price_include_override != "tax_included",
        )
        if added:
            raise UserError(
                self.env._(
                    "%(taxes)s would be added to the price the customer already "
                    "paid. Set them to be included in the price.",
                    taxes=", ".join(added.mapped("name")),
                ),
            )

    # -- commerce ----------------------------------------------------------

    def _canonical_key(self, row):
        return f"commerce:{row.external_order_id}"

    def _materialise_order(self, row):
        """Return the native sale for one order this drop is adding."""
        self.ensure_one()
        existing = self.env["b2c.order"].search(
            [
                ("company_id", "=", self.company_id.id),
                ("canonical_key", "=", self._canonical_key(row)),
            ],
            limit=1,
        )
        if existing:
            row.write({"resolution": "known", "order_id": existing.id})
            return existing.sale_order_id
        partner = self._partner_for(row)
        money = self._order_money(row)
        order = self._commerce_order(row, partner, money)
        sale = self._sale_order(row, order, partner, money)
        order.with_context(**self._context()).write(
            {"sale_order_id": sale.id, "partner_id": partner.id, "shipping_partner_id": partner.id},
        )
        row.write({"resolution": "known", "order_id": order.id})
        return sale

    def _commerce_order(self, row, partner, money):
        """Return the canonical order the channels agree on."""
        self.ensure_one()
        values = row.values or {}
        channel = self._channels()[row.provider]
        currency = self._currency(values)
        order = self._trusted("b2c.order").create(
            {
                "name": f"B2C {row.external_order_id}",
                "canonical_key": self._canonical_key(row),
                "company_id": self.company_id.id,
                "channel_id": channel.id,
                "source_provider": row.provider,
                "origin": "imported",
                "business_purpose": "sale",
                "external_order_id": row.external_order_id,
                "external_display_id": row.external_order_id,
                "original_provider_state": values.get("original_provider_state") or False,
                "state": "fulfilled",
                "order_date": row.occurred_at,
                "customer_name": partner.name,
                "customer_email": values.get("customer_email") or False,
                "shipping_name": values.get("shipping_name") or False,
                "shipping_street": values.get("shipping_street") or False,
                "shipping_city": values.get("shipping_city") or False,
                "shipping_zip": values.get("shipping_zip") or False,
                "original_country": values.get("original_country") or False,
                "country_id": partner.country_id.id,
                "currency_id": currency.id,
                "subtotal_amount": float(money["sold"]),
                "shipping_amount": float(money["shipping"]),
                "discount_amount": float(money["discount"]),
                "fee_amount": float(money["fee"]),
                "total_amount": float(money["total"]),
                "mapping_state": "verified",
                "review_state": "reviewed",
                "amount_completeness": "complete" if "total_amount" in values else "partial",
                "fulfilment_mode": self._fulfilment_mode(row),
            },
        )
        self._commerce_lines(row, order)
        self._commerce_source(row, order)
        return order

    def _fulfilment_mode(self, row):
        modes = {
            line.product_id.b2c_fulfilment_mode
            for line in self._lines_of(row)
            if line.product_id
        }
        if modes == {"printful"}:
            return "printful"
        if modes == {"own_stock"}:
            return "own_stock"
        return "mixed" if len(modes) > 1 else "unknown"

    def _currency(self, values):
        code = (values.get("currency") or "EUR").strip().upper()
        return self.env["res.currency"].with_context(active_test=False).search(
            [("name", "=", code)], limit=1,
        ) or self.company_id.currency_id

    def _commerce_lines(self, row, order):
        self.ensure_one()
        Line = self._trusted("b2c.order.line")
        for sequence, line in enumerate(self._lines_of(row), start=1):
            values = line.values or {}
            Line.create(
                {
                    "order_id": order.id,
                    "line_key": f"{row.provider}:{row.external_order_id}:{line.external_line_id}",
                    "sequence": sequence * 10,
                    "external_line_id": line.external_line_id or False,
                    "external_transaction_id": line.external_line_id or False,
                    "external_listing_id": values.get("external_listing_id") or False,
                    "original_sku": values.get("original_sku") or False,
                    "original_name": values.get("original_name") or "",
                    "original_variation": values.get("original_variation") or False,
                    "quantity": float(self._decimal(values, "quantity")),
                    "unit_price": float(self._decimal(values, "unit_price")),
                    "subtotal_amount": float(self._decimal(values, "subtotal_amount")),
                    "product_id": line.product_id.id,
                    "alias_id": line.alias_id.id,
                    "mapping_state": "verified",
                    "amount_completeness": "complete",
                },
            )

    def _evidence(self, row):
        """Return the retained provider payload this row came from.

        The payload carries customer names and addresses, so it lives in the
        access-scoped evidence model rather than in the batch, and it is kept
        once: the same payload read twice is the same evidence.
        """
        self.ensure_one()
        source = row.file_id
        checksum = source.checksum or row.payload_digest
        Evidence = self.env["b2c.provider.evidence"].sudo().with_context(
            b2c_evidence_import=True,
        )
        found = Evidence.search(
            [
                ("company_id", "=", self.company_id.id),
                ("source_checksum", "=", checksum),
                ("payload_digest", "=", row.payload_digest),
            ],
            limit=1,
        )
        if found:
            return found
        return Evidence.create(
            {
                "evidence_key": f"{row.format_id}:{checksum}:{row.payload_digest}",
                "company_id": self.company_id.id,
                "source_provider": row.provider,
                "source_name": source.name or self.env._("Printful orders"),
                "source_checksum": checksum,
                "schema_digest": source.schema_digest or row.format_id,
                "payload_digest": row.payload_digest,
                "payload_json": row.payload,
                "contains_pii": True,
                "occurred_at": row.occurred_at,
            },
        )

    def _commerce_source(self, row, order):
        self.ensure_one()
        self._trusted("b2c.order.source").create(
            {
                "order_id": order.id,
                "evidence_id": self._evidence(row).id,
                "source_provider": row.provider,
                "origin": "imported",
                "source_record_key": f"{row.format_id}:{row.external_order_id}",
                "external_order_id": row.external_order_id,
                "original_provider_state": (row.values or {}).get("original_provider_state") or False,
                "source_precedence": 10,
                "is_primary": True,
                "completeness_state": "complete",
                "provider_payload_digest": row.payload_digest,
            },
        )

    # -- native sales ------------------------------------------------------

    def _export_position(self, partner):
        """Return the position a sale outside the Union is stated under.

        Every position that could answer outside the Union charges nothing, so
        which one answers changes no number; it changes whether the invoice can
        say what kind of supply it was.  Odoo picks a position from the
        customer, and no customer record distinguishes a consumer buying goods
        from a business buying services, so this is named rather than deduced.
        """
        self.ensure_one()
        position = self.company_id.usl_b2c_export_position_id
        if not position:
            return self.env["account.fiscal.position"]
        if partner.country_id in self.env.ref("base.europe").country_ids:
            return self.env["account.fiscal.position"]
        return position

    def _sale_order(self, row, order, partner, money):
        """Return the confirmed sales order, totalling what the customer paid."""
        self.ensure_one()
        channel = self._channels()[row.provider]
        currency = order.currency_id
        sale = self._trusted("sale.order").create(
            {
                "company_id": self.company_id.id,
                "partner_id": partner.id,
                "partner_invoice_id": partner.id,
                "partner_shipping_id": partner.id,
                "date_order": row.occurred_at,
                "pricelist_id": self._pricelist(currency).id,
                "currency_id": currency.id,
                "client_order_ref": row.external_order_id,
                "origin": self.env._("Channel import %(batch)s", batch=self.name),
                "usl_b2c_order_id": order.id,
            }
            | (
                {"fiscal_position_id": position.id}
                if (position := self._export_position(partner))
                else {}
            ),
        )
        discount = float(self._discount_percent(money))
        costs = {
            (cost["sku"], cost["name"]): cost
            for cost in self._supplier_costs().get(row.id, ())
        }
        for sequence, line in enumerate(self._lines_of(row), start=1):
            values = line.values or {}
            self._trusted("sale.order.line").create(
                {
                    "order_id": sale.id,
                    "product_id": line.product_id.id,
                    "name": values.get("original_name") or line.product_id.display_name,
                    "product_uom_qty": float(self._decimal(values, "quantity")),
                    "price_unit": float(self._decimal(values, "unit_price")),
                    "discount": discount,
                    "sequence": sequence * 10,
                    "purchase_price": float(self._supplier_unit_cost(costs, values)),
                },
            )
        if money["shipping"]:
            self._trusted("sale.order.line").create(
                {
                    "order_id": sale.id,
                    "product_id": channel.shipping_product_id.id,
                    "name": self.env._("Carriage charged by %(channel)s", channel=channel.name),
                    "product_uom_qty": 1,
                    "price_unit": float(money["shipping"]),
                    "sequence": 9000,
                },
            )
        self._assert_destination_tax(sale)
        self._assert_total(sale, money, row)
        sale.action_confirm()
        # Confirming stamps a quotation with the day it became an order, which
        # for a sale that already happened is the day it happened.
        sale.write({"date_order": row.occurred_at})
        self._deliver(sale, row)
        return sale

    def _deliver(self, sale, row):
        """Send what the sale sold, on the day it actually went out.

        A channel that says an order shipped has already shipped it, so the
        delivery is completed rather than left waiting: stock has to fall on
        the day the goods left, not on the day the export was read.
        """
        self.ensure_one()
        shipped = self._shipped_on(row)
        if not shipped:
            return
        for picking in sale.picking_ids.sudo():
            for move in picking.move_ids:
                move.picked = True
                move.quantity = move.product_uom_qty
            try:
                picking.with_context(skip_backorder=True).button_validate()
            except UserError as error:
                self._raise_issue(
                    "delivery_incomplete",
                    self.env._(
                        "%(order)s could not be delivered from stock",
                        order=row.external_order_id,
                    ),
                    external_order_id=row.external_order_id,
                    row=row,
                    severity="advisory",
                    note=str(error),
                )
                continue
            picking.write({"date_done": shipped})
            picking.move_ids.write({"date": shipped})

    def _shipped_on(self, row):
        """Return when the goods left, from the channel or from the supplier."""
        self.ensure_one()
        stated = (row.values or {}).get("fulfilment_date")
        if stated:
            return stated
        for fulfilment in self._fulfilment_rows():
            if fulfilment.fulfilment_of_row_id == row:
                return fulfilment.occurred_at
        return None

    def _supplier_unit_cost(self, costs, values):
        """Return what the supplier charged for one of this line, if it says."""
        sku = (values.get("original_sku") or "").strip()
        name = (values.get("original_name") or "").strip()
        for key, cost in costs.items():
            if sku and key[0] == sku:
                return cost["unit_cost"]
        for key, cost in costs.items():
            if name and key[1].startswith(name[:20]):
                return cost["unit_cost"]
        if len(costs) == 1:
            return next(iter(costs.values()))["unit_cost"]
        return Decimal("0")

    def _assert_total(self, sale, money, row):
        """Prove the sale totals exactly what the channel says was paid."""
        self.ensure_one()
        expected = float(money["total"])
        if float_compare(sale.amount_total, expected, precision_rounding=sale.currency_id.rounding):
            raise UserError(
                self.env._(
                    "Order %(order)s totals %(built)s, not the %(paid)s the channel "
                    "says was paid.",
                    order=row.external_order_id,
                    built=sale.amount_total,
                    paid=expected,
                ),
            )

    def _pricelist(self, currency):
        self.ensure_one()
        pricelist = self.env["product.pricelist"].sudo().search(
            [
                ("currency_id", "=", currency.id),
                ("company_id", "in", [False, self.company_id.id]),
            ],
            limit=1,
        )
        if pricelist:
            return pricelist
        return self.env["product.pricelist"].sudo().create(
            {
                "name": self.env._("B2C %(currency)s", currency=currency.name),
                "currency_id": currency.id,
                "company_id": self.company_id.id,
            },
        )
