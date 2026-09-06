"""Purchases, receipts, unbuilds, manufacturing and deliveries."""

from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal

from odoo import Command, fields, models
from odoo.exceptions import UserError

from odoo.addons.usl_b2c_restore.native_plan import (
    ACQUISITIONS,
    EXPECTED_THEORETICAL_STOCK,
    PACK_COMPONENTS,
    source_line_components,
    stock_disposition,
)

from .comparison import (
    BILL_EVIDENCE_COUNTS,
    assert_values,
    materialization_context,
)


class UslB2cNativeInventoryMaterializer(models.AbstractModel):
    _name = "usl.b2c.native.inventory.materializer"
    _description = "USL Native B2C Inventory Materializer"

    def _ctx(self):
        return materialization_context()

    def _assert_values(self, record, expected, label):
        assert_values(record, expected, label)

    def _quantity_digits(self):
        """Return the precision Odoo stores a stock quantity with."""
        return self.env["stock.move"]._fields["product_uom_qty"].get_digits(self.env)[1]

    def _assert_landed_cost_supported(self, company, picking, key):
        """Explain up front what a landed cost needs, instead of failing opaquely.

        Odoo only spreads an additional cost over receipt layers when the
        product keeps a moving cost, and it needs a journal even when periodic
        valuation writes no entry.
        """
        products = picking.move_ids.filtered(
            lambda move: move.state != "cancel",
        ).product_id.with_company(company)
        unsupported = products.filtered(
            lambda product: product.cost_method not in {"fifo", "average"},
        )
        if unsupported:
            raise UserError(
                f"Historical landed cost {key} needs FIFO or average costing. "
                f"These products use standard cost: "
                f"{', '.join(sorted(unsupported.mapped('default_code')))}.",
            )
        journal = self.env["stock.landed.cost"].with_company(
            company,
        )._default_account_journal_id()
        if not journal:
            raise UserError(
                f"Historical landed cost {key} needs a stock journal on the "
                f"company or its product categories.",
            )

    def _drop_historical_vendor_prices(self, sellers_before):
        """Remove the vendor pricelist rows a historical confirmation created."""
        created = (
            self.env["product.supplierinfo"]
            .sudo()
            .with_context(active_test=False)
            .search([("id", "not in", list(sellers_before))])
        )
        count = len(created)
        created.unlink()
        return count

    def _move_signature(self, product, quantity, extra, source_key):
        return (
            product.id,
            round(float(quantity), self._quantity_digits()),
            product.uom_id.id,
            extra.get("purchase_line_id") or False,
            extra.get("sale_line_id") or False,
            extra.get("b2c_line_id") or False,
            extra.get("source_key") or source_key,
        )

    def _record_move_signature(self, move):
        return (
            move.product_id.id,
            round(move.product_uom_qty, self._quantity_digits()),
            move.uom_id.id,
            move.purchase_line_id.id or False,
            move.sale_line_id.id or False,
            move.usl_b2c_order_line_id.id or False,
            move.usl_b2c_source_key or False,
        )

    def _validate_direct_picking(
        self,
        picking,
        *,
        company,
        key,
        date,
        partner,
        moves,
        picking_type,
        location,
        location_dest,
        completed,
    ):
        expected_states = (
            {"done"} if completed else {"confirmed", "waiting", "assigned"}
        )
        self._assert_values(
            picking,
            {
                "company_id": company,
                "partner_id": partner,
                "picking_type_id": picking_type,
                "location_id": location,
                "location_dest_id": location_dest,
                "scheduled_date": date,
                "origin": key,
                "usl_historical_b2c": True,
                "usl_b2c_source_key": key,
            },
            f"Historical stock operation {key}",
        )
        if picking.state not in expected_states:
            raise UserError(
                f"Historical stock operation {key} has state {picking.state!r}; "
                f"expected one of {sorted(expected_states)!r}.",
            )
        if completed:
            self._assert_values(
                picking,
                {"date_done": date},
                f"Historical stock operation {key}",
            )
        expected_moves = Counter(
            self._move_signature(product, quantity, extra, key)
            for product, quantity, extra in moves
        )
        actual_moves = Counter(
            self._record_move_signature(move)
            for move in picking.move_ids.filtered(lambda move: move.state != "cancel")
        )
        if actual_moves != expected_moves:
            raise UserError(
                f"Historical stock operation {key} has drifted move lines: "
                f"{actual_moves!r} != {expected_moves!r}.",
            )

    def _product(self, code, *, allow_sample=False, company=None, apply=False):
        products = (
            self.env["product.product"]
            .sudo()
            .with_context(active_test=True)
            .search([("default_code", "=", code)], limit=2)
        )
        if not products and allow_sample:
            if not apply:
                return self.env["product.product"]
            template = self.env["product.template"].sudo().with_context(**self._ctx()).create(
                {
                    "name": "Quandun 40 mm prototype samples — October 2025",
                    "default_code": code,
                    "company_id": company.id,
                    "type": "consu",
                    "is_storable": True,
                    "sale_ok": False,
                    "purchase_ok": True,
                    "tracking": "none",
                    "b2c_catalog_classification": "legacy",
                    "b2c_fulfilment_mode": "not_applicable",
                    "b2c_inventory_role": "ordinary",
                    "b2c_opening_stock_state": "not_applicable",
                },
            )
            products = template.product_variant_id
        if len(products) != 1:
            raise UserError(f"Internal reference {code!r} must identify exactly one product.")
        return products

    def _inventory_loss_location(self, company, products, source_key):
        location_ids = {
            product.with_company(company).property_stock_inventory.id
            for product in products
            if product.with_company(company).property_stock_inventory
        }
        if len(location_ids) != 1:
            raise UserError(
                f"{source_key} has no unique company inventory-loss location.",
            )
        return self.env["stock.location"].browse(location_ids.pop())

    def _bill_line(self, company, acquisition_line, partner):
        domain = [
            ("move_id.company_id", "=", company.id),
            ("move_id.partner_id", "=", partner.id),
            ("move_id.move_type", "in", ["in_invoice", "in_refund"]),
            ("move_id.ref", "=", acquisition_line["bill_ref"]),
            ("name", "=", acquisition_line["bill_label"]),
            ("display_type", "=", "product"),
        ]
        lines = self.env["account.move.line"].sudo().search(domain, limit=2)
        if len(lines) != 1:
            raise UserError(
                f"Supplier evidence is not unique for {acquisition_line['bill_ref']!r} / "
                f"{acquisition_line['bill_label']!r}.",
            )
        if lines.move_id.state == "cancel":
            raise UserError(
                f"Supplier evidence {lines.move_id.display_name!r} is cancelled.",
            )
        return lines

    def _validate_acquisitions(self, company, apply):
        result = []
        for acquisition in ACQUISITIONS:
            partners = (
                self.env["res.partner"]
                .sudo()
                .with_context(active_test=False)
                .search([("name", "=", acquisition["partner"])], limit=2)
            )
            if len(partners) != 1:
                raise UserError(f"Supplier {acquisition['partner']!r} is not unique.")
            currency = self.env["res.currency"].sudo().with_context(active_test=False).search(
                [("name", "=", acquisition["currency"])], limit=2,
            )
            if len(currency) != 1:
                raise UserError(f"Currency {acquisition['currency']!r} is not unique.")
            lines = []
            for item in acquisition["lines"]:
                product = self._product(
                    item["code"],
                    allow_sample=item["code"] == "B2C-SAMPLE-QD40-2025",
                    company=company,
                    apply=apply,
                )
                bill_line = self._bill_line(company, item, partners)
                lines.append((item, product, bill_line))
            result.append((acquisition, partners, currency, lines))
        return result

    def _theoretical_ledger(self, orders, metadata):
        acquired = defaultdict(Decimal)
        consumed = defaultdict(Decimal)
        reserved = defaultdict(Decimal)
        for acquisition in ACQUISITIONS:
            if acquisition.get("internal_consumption"):
                continue
            for line in acquisition["lines"]:
                components = PACK_COMPONENTS.get(line["code"])
                if components:
                    for code, per_pack in components.items():
                        acquired[code] += line["quantity"] * per_pack
                else:
                    acquired[line["code"]] += line["quantity"]
        for order in orders:
            order_metadata = metadata[order.id]
            for line in order.line_ids:
                product = line.product_id
                mode = product.product_tmpl_id.b2c_fulfilment_mode
                disposition = stock_disposition(
                    order_metadata["state"],
                    order_metadata["source_fulfilment_state"],
                    order.external_display_id,
                    mode,
                )
                if disposition in {"pod", "cancelled"}:
                    continue
                requirements = source_line_components(
                    line.original_name,
                    line.original_variation,
                    line.quantity,
                )
                if not requirements:
                    requirements = {product.default_code: Decimal(str(line.quantity))}
                target = reserved if disposition == "reserved" else consumed
                for code, quantity in requirements.items():
                    target[code] += quantity
        actual = {
            code: (acquired[code] - consumed[code], reserved[code])
            for code in EXPECTED_THEORETICAL_STOCK
        }
        if actual != EXPECTED_THEORETICAL_STOCK:
            differences = {
                code: {"actual": actual.get(code), "expected": expected}
                for code, expected in EXPECTED_THEORETICAL_STOCK.items()
                if actual.get(code) != expected
            }
            raise UserError(f"The source-derived theoretical inventory changed: {differences!r}")
        return {
            code: {
                "acquired": str(acquired[code]),
                "consumed": str(consumed[code]),
                "on_hand": str(actual[code][0]),
                "reserved": str(actual[code][1]),
                "available": str(actual[code][0] - actual[code][1]),
            }
            for code in EXPECTED_THEORETICAL_STOCK
        }

    def _warehouse(self, company):
        warehouses = self.env["stock.warehouse"].sudo().search([("company_id", "=", company.id)], limit=2)
        if len(warehouses) != 1:
            raise UserError("Historical B2C stock requires exactly one existing USL warehouse.")
        return warehouses

    def _done_picking(
        self,
        company,
        warehouse,
        key,
        date,
        partner,
        moves,
        picking_type,
        *,
        source_location=None,
        destination_location=None,
    ):
        existing = self.env["stock.picking"].sudo().search(
            [("company_id", "=", company.id), ("usl_b2c_source_key", "=", key)],
            limit=1,
        )
        if existing:
            self._validate_direct_picking(
                existing,
                company=company,
                key=key,
                date=date,
                partner=partner,
                moves=moves,
                picking_type=picking_type,
                location=source_location or picking_type.default_location_src_id,
                location_dest=(
                    destination_location or picking_type.default_location_dest_id
                ),
                completed=True,
            )
            return existing
        location_id = source_location or picking_type.default_location_src_id
        location_dest_id = destination_location or picking_type.default_location_dest_id
        picking = self.env["stock.picking"].sudo().with_context(**self._ctx()).create(
            {
                "picking_type_id": picking_type.id,
                "partner_id": partner.id if partner else False,
                "company_id": company.id,
                "location_id": location_id.id,
                "location_dest_id": location_dest_id.id,
                "scheduled_date": date,
                "origin": key,
                "usl_historical_b2c": True,
                "usl_b2c_source_key": key,
            },
        )
        records = self.env["stock.move"]
        for product, quantity, extra in moves:
            records |= self.env["stock.move"].sudo().with_context(**self._ctx()).create(
                {
                    "product_id": product.id,
                    "product_uom_qty": float(quantity),
                    "uom_id": product.uom_id.id,
                    "picking_id": picking.id,
                    "company_id": company.id,
                    "location_id": location_id.id,
                    "location_dest_id": location_dest_id.id,
                    "date": date,
                    "purchase_line_id": extra.get("purchase_line_id") or False,
                    "sale_line_id": extra.get("sale_line_id") or False,
                    "usl_b2c_order_line_id": extra.get("b2c_line_id") or False,
                    "usl_b2c_source_key": extra.get("source_key") or key,
                },
            )
        records._action_confirm(merge=False)
        for move in records:
            move.write({"quantity": move.product_uom_qty, "picked": True})
        records._action_done(cancel_backorder=True)
        records.write({"date": date})
        records.move_line_ids.write({"date": date})
        picking.write({"date_done": date})
        return picking

    def _reserve_picking(self, company, key, date, partner, moves, picking_type, b2c_order):
        existing = self.env["stock.picking"].sudo().search(
            [("company_id", "=", company.id), ("usl_b2c_source_key", "=", key)],
            limit=1,
        )
        if existing:
            self._validate_direct_picking(
                existing,
                company=company,
                key=key,
                date=date,
                partner=partner,
                moves=moves,
                picking_type=picking_type,
                location=picking_type.default_location_src_id,
                location_dest=picking_type.default_location_dest_id,
                completed=False,
            )
            self._assert_values(
                existing,
                {"usl_b2c_order_id": b2c_order},
                f"Historical stock operation {key}",
            )
            return existing
        picking = self.env["stock.picking"].sudo().with_context(**self._ctx()).create(
            {
                "picking_type_id": picking_type.id,
                "partner_id": partner.id if partner else False,
                "company_id": company.id,
                "location_id": picking_type.default_location_src_id.id,
                "location_dest_id": picking_type.default_location_dest_id.id,
                "scheduled_date": date,
                "origin": key,
                "usl_historical_b2c": True,
                "usl_b2c_source_key": key,
                "usl_b2c_order_id": b2c_order.id if b2c_order else False,
            },
        )
        move_records = self.env["stock.move"]
        for product, quantity, extra in moves:
            move_records |= self.env["stock.move"].sudo().with_context(**self._ctx()).create(
                {
                    "product_id": product.id,
                    "product_uom_qty": float(quantity),
                    "uom_id": product.uom_id.id,
                    "picking_id": picking.id,
                    "company_id": company.id,
                    "location_id": picking.location_id.id,
                    "location_dest_id": picking.location_dest_id.id,
                    "date": date,
                    "sale_line_id": extra.get("sale_line_id") or False,
                    "usl_b2c_order_line_id": extra.get("b2c_line_id") or False,
                    "usl_b2c_source_key": extra.get("source_key") or key,
                },
            )
        move_records._action_confirm(merge=False)
        move_records._action_assign()
        return picking

    def _ensure_pack_bom(self, company, pack, components):
        boms = self.env["mrp.bom"].sudo().with_context(active_test=False).search(
            [("product_id", "=", pack.id), ("type", "=", "normal"), ("company_id", "in", [False, company.id])],
        )
        expected = {
            self._product(code).id: float(quantity)
            for code, quantity in components.items()
        }
        exact = boms.filtered(
            lambda bom: bom.product_qty == 1
            and {line.product_id.id: line.product_qty for line in bom.bom_line_ids} == expected
        )
        if len(exact) == 1:
            if boms - exact:
                raise UserError(
                    f"Supplier pack {pack.display_name} has additional conflicting BoMs."
                )
            if not exact.active:
                exact.active = True
            return exact
        if boms:
            raise UserError(
                f"Supplier pack {pack.display_name} has a conflicting BoM."
            )
        return self.env["mrp.bom"].sudo().with_context(**self._ctx()).create(
            {
                "product_tmpl_id": pack.product_tmpl_id.id,
                "product_id": pack.id,
                "product_qty": 1,
                "uom_id": pack.uom_id.id,
                "type": "normal",
                "company_id": company.id,
                "bom_line_ids": [
                    Command.create(
                        {
                            "product_id": self._product(code).id,
                            "product_qty": float(quantity),
                            "uom_id": self._product(code).uom_id.id,
                        },
                    )
                    for code, quantity in components.items()
                ],
            },
        )

    def _unbuild_pack(self, company, warehouse, key, date, pack, quantity):
        existing = self.env["mrp.unbuild"].sudo().search(
            [("company_id", "=", company.id), ("usl_b2c_source_key", "=", key)], limit=1,
        )
        if existing:
            bom = self._ensure_pack_bom(
                company,
                pack,
                PACK_COMPONENTS[pack.default_code],
            )
            self._assert_values(
                existing,
                {
                    "company_id": company,
                    "product_id": pack,
                    "product_qty": quantity,
                    "uom_id": pack.uom_id,
                    "bom_id": bom,
                    "location_id": warehouse.lot_stock_id,
                    "location_dest_id": warehouse.lot_stock_id,
                    "usl_historical_b2c": True,
                    "usl_b2c_source_key": key,
                    "state": "done",
                },
                f"Historical supplier-pack conversion {key}",
            )
            return existing
        bom = self._ensure_pack_bom(company, pack, PACK_COMPONENTS[pack.default_code])
        unbuild = self.env["mrp.unbuild"].sudo().with_context(**self._ctx()).create(
            {
                "product_id": pack.id,
                "product_qty": float(quantity),
                "uom_id": pack.uom_id.id,
                "bom_id": bom.id,
                "company_id": company.id,
                "location_id": warehouse.lot_stock_id.id,
                "location_dest_id": warehouse.lot_stock_id.id,
                "usl_historical_b2c": True,
                "usl_b2c_source_key": key,
            },
        )
        unbuild.action_unbuild()
        (unbuild.consume_line_ids | unbuild.produce_line_ids).write({"date": date})
        (unbuild.consume_line_ids | unbuild.produce_line_ids).move_line_ids.write({"date": date})
        return unbuild

    def _validate_existing_purchase(
        self,
        company,
        warehouse,
        acquisition,
        partner,
        currency,
        lines,
        purchase,
    ):
        date = fields.Datetime.to_datetime(acquisition["date"])
        self._assert_values(
            purchase,
            {
                "partner_id": partner,
                "company_id": company,
                "currency_id": currency,
                "date_order": date,
                "origin": f"Historical B2C acquisition {acquisition['key']}",
                "usl_historical_b2c": True,
                "usl_b2c_source_key": acquisition["key"],
                "state": "purchase",
            },
            f"Historical purchase {acquisition['key']}",
        )
        purchase_lines = purchase.order_line.filtered(lambda line: not line.display_type)
        if len(purchase_lines) != len(lines):
            raise UserError(
                f"Historical purchase {acquisition['key']} has {len(purchase_lines)} "
                f"product lines; expected {len(lines)}.",
            )
        lines_by_product = {line.product_id.id: line for line in purchase_lines}
        if len(lines_by_product) != len(purchase_lines):
            raise UserError(
                f"Historical purchase {acquisition['key']} has duplicate product lines.",
            )
        for item, product, bill_line in lines:
            line = lines_by_product.get(product.id)
            if not line:
                raise UserError(
                    f"Historical purchase {acquisition['key']} is missing {product.display_name}.",
                )
            self._assert_values(
                line,
                {
                    "product_id": product,
                    "name": item["bill_label"],
                    "product_qty": item["quantity"],
                    "uom_id": product.uom_id,
                    "price_unit": item["price"],
                    "date_planned": date,
                    "usl_source_bill_line_ids": bill_line,
                },
                f"Historical purchase line {acquisition['key']} / {product.default_code}",
            )
            if line.tax_ids:
                raise UserError(
                    f"Historical purchase line {line.display_name} unexpectedly has taxes.",
                )
            if (
                BILL_EVIDENCE_COUNTS[(item["bill_ref"], item["bill_label"])] == 1
                and bill_line.purchase_line_id != line
            ):
                raise UserError(
                    f"Vendor-bill line {bill_line.display_name!r} lost its exact "
                    "historical Purchase-line relation.",
                )
        receipts = purchase.picking_ids.filtered(lambda record: record.state != "cancel")
        if len(receipts) != 1:
            raise UserError(
                f"Historical purchase {acquisition['key']} has {len(receipts)} receipts; "
                "expected one.",
            )
        receipt = receipts[0]
        self._assert_values(
            receipt,
            {
                "company_id": company,
                "picking_type_id": warehouse.in_type_id,
                "location_id": warehouse.in_type_id.default_location_src_id,
                "location_dest_id": warehouse.in_type_id.default_location_dest_id,
                "scheduled_date": date,
                "date_done": date,
                "usl_historical_b2c": True,
                "usl_b2c_source_key": f"receipt:{acquisition['key']}",
                "state": "done",
            },
            f"Historical receipt {acquisition['key']}",
        )
        receipt_moves = receipt.move_ids.filtered(lambda move: move.state != "cancel")
        digits = self._quantity_digits()
        expected_receipt_moves = Counter(
            (
                product.id,
                round(float(item["quantity"]), digits),
                lines_by_product[product.id].id,
            )
            for item, product, _bill_line in lines
        )
        actual_receipt_moves = Counter(
            (
                move.product_id.id,
                round(move.product_uom_qty, digits),
                move.purchase_line_id.id or False,
            )
            for move in receipt_moves
        )
        if actual_receipt_moves != expected_receipt_moves:
            raise UserError(
                f"Historical receipt {acquisition['key']} drifted: "
                f"{actual_receipt_moves!r} != {expected_receipt_moves!r}.",
            )
        for item, product, _bill_line in lines:
            if product.default_code in PACK_COMPONENTS:
                self._unbuild_pack(
                    company,
                    warehouse,
                    f"unpack:{acquisition['key']}:{product.default_code}",
                    date,
                    product,
                    item["quantity"],
                )
        if acquisition.get("internal_consumption"):
            inventory_location = self._inventory_loss_location(
                company,
                [product for _item, product, _bill_line in lines],
                acquisition["key"],
            )
            self._done_picking(
                company,
                warehouse,
                f"internal-consumption:{acquisition['key']}",
                date,
                False,
                [
                    (
                        product,
                        item["quantity"],
                        {"name": "Documented prototype consumption"},
                    )
                    for item, product, _bill_line in lines
                ],
                warehouse.int_type_id,
                source_location=warehouse.lot_stock_id,
                destination_location=inventory_location,
            )
        self._landed_cost(company, acquisition, receipt)

    def _purchase(self, run, company, warehouse, acquisition, partner, currency, lines):
        existing = self.env["purchase.order"].sudo().search(
            [("company_id", "=", company.id), ("usl_b2c_source_key", "=", acquisition["key"])], limit=1,
        )
        if existing:
            self._validate_existing_purchase(
                company,
                warehouse,
                acquisition,
                partner,
                currency,
                lines,
                existing,
            )
            return existing
        date = fields.Datetime.to_datetime(acquisition["date"])
        purchase = self.env["purchase.order"].sudo().with_context(**self._ctx()).create(
            {
                "partner_id": partner.id,
                "company_id": company.id,
                "currency_id": currency.id,
                "date_order": date,
                "origin": f"Historical B2C acquisition {acquisition['key']}",
                "usl_historical_b2c": True,
                "usl_b2c_source_key": acquisition["key"],
            },
        )
        purchase_lines = []
        for item, product, bill_line in lines:
            line = self.env["purchase.order.line"].sudo().with_context(**self._ctx()).create(
                {
                    "order_id": purchase.id,
                    "product_id": product.id,
                    "name": item["bill_label"],
                    "product_qty": float(item["quantity"]),
                    "uom_id": product.uom_id.id,
                    "price_unit": float(item["price"]),
                    "date_planned": date,
                    "tax_ids": [Command.clear()],
                    "usl_source_bill_line_ids": [Command.link(bill_line.id)],
                },
            )
            if BILL_EVIDENCE_COUNTS[(item["bill_ref"], item["bill_label"])] == 1:
                if bill_line.purchase_line_id and bill_line.purchase_line_id != line:
                    raise UserError(
                        f"Vendor-bill line {bill_line.display_name!r} is already linked "
                        "to another Purchase line.",
                    )
                bill_line.with_context(
                    **self._ctx(),
                    check_move_validity=False,
                ).write({"purchase_line_id": line.id})
            purchase_lines.append((line, item, product))
        # Confirming a Purchase order teaches Odoo the vendor price it just saw.
        # These are historical prices, so the run removes the vendor pricelist
        # rows its own confirmation created and leaves product data untouched.
        sellers_before = set(
            self.env["product.supplierinfo"].sudo().with_context(active_test=False).search([]).ids,
        )
        purchase.button_confirm()
        self._drop_historical_vendor_prices(sellers_before)
        picking = purchase.picking_ids.filtered(lambda record: record.state != "cancel")
        if len(picking) != 1:
            raise UserError(f"Acquisition {acquisition['key']} did not create one receipt.")
        picking.write(
            {
                "usl_historical_b2c": True,
                "usl_b2c_source_key": f"receipt:{acquisition['key']}",
                "scheduled_date": date,
            },
        )
        moves = picking.move_ids.filtered(lambda move: move.state != "cancel")
        moves._action_confirm(merge=False)
        for move in moves:
            move.quantity = move.product_uom_qty
            move.picked = True
        moves._action_done(cancel_backorder=True)
        moves.write({"date": date})
        moves.move_line_ids.write({"date": date})
        picking.write({"date_done": date})
        for line, item, product in purchase_lines:
            if product.default_code in PACK_COMPONENTS:
                self._unbuild_pack(
                    company,
                    warehouse,
                    f"unpack:{acquisition['key']}:{product.default_code}",
                    date,
                    product,
                    item["quantity"],
                )
        if acquisition.get("internal_consumption"):
            inventory_location = self._inventory_loss_location(
                company,
                [product for _line, _item, product in purchase_lines],
                acquisition["key"],
            )
            # An explicit stock move is clearer than manufacturing a sale or scrap reason.
            self._done_picking(
                company,
                warehouse,
                f"internal-consumption:{acquisition['key']}",
                date,
                False,
                [(product, item["quantity"], {"name": "Documented prototype consumption"}) for _line, item, product in purchase_lines],
                warehouse.int_type_id,
                source_location=warehouse.lot_stock_id,
                destination_location=inventory_location,
            )
        self._landed_cost(company, acquisition, picking)
        return purchase

    def _landed_cost(self, company, acquisition, picking):
        spec = acquisition.get("landed_cost")
        if not spec:
            return self.env["stock.landed.cost"]
        existing = self.env["stock.landed.cost"].sudo().search(
            [("company_id", "=", company.id), ("usl_b2c_source_key", "=", spec["key"])], limit=1,
        )
        if existing:
            self._assert_values(
                existing,
                {
                    "date": acquisition["date"],
                    "company_id": company,
                    "picking_ids": picking,
                    "usl_historical_b2c": True,
                    "usl_b2c_source_key": spec["key"],
                    "state": "done",
                },
                f"Historical landed cost {spec['key']}",
            )
            if len(existing.cost_lines) != 1:
                raise UserError(
                    f"Historical landed cost {spec['key']} must have one cost line.",
                )
            self._assert_values(
                existing.cost_lines,
                {
                    "name": spec["label"],
                    "price_unit": spec["amount"],
                    "split_method": spec["split_method"],
                },
                f"Historical landed-cost line {spec['key']}",
            )
            if existing.account_move_id:
                raise UserError(
                    f"Manual-valuation landed cost {existing.name} unexpectedly has Accounting.",
                )
            return existing
        self._assert_landed_cost_supported(company, picking, spec["key"])
        product = self.env["product.product"].sudo().search(
            [("default_code", "=", "B2C-HISTORICAL-LANDED-COST")], limit=1,
        )
        if not product:
            product = self.env["product.template"].sudo().with_context(**self._ctx()).create(
                {
                    "name": "Documented historical B2C inbound freight and duty",
                    "default_code": "B2C-HISTORICAL-LANDED-COST",
                    "type": "service",
                    "sale_ok": False,
                    "purchase_ok": True,
                    "landed_cost_ok": True,
                    "split_method_landed_cost": spec["split_method"],
                    "company_id": company.id,
                },
            ).product_variant_id
        cost = self.env["stock.landed.cost"].sudo().with_context(**self._ctx()).create(
            {
                "date": acquisition["date"],
                "company_id": company.id,
                "picking_ids": [Command.link(picking.id)],
                "usl_historical_b2c": True,
                "usl_b2c_source_key": spec["key"],
                "cost_lines": [
                    Command.create(
                        {
                            "name": spec["label"],
                            "product_id": product.id,
                            "price_unit": float(spec["amount"]),
                            "split_method": spec["split_method"],
                        },
                    ),
                ],
            },
        )
        cost.compute_landed_cost()
        cost.button_validate()
        if cost.account_move_id:
            raise UserError(f"Manual-valuation landed cost {cost.name} unexpectedly created Accounting.")
        return cost

    def _ensure_finished_bom(self, company, product, components):
        expected = {self._product(code).id: float(quantity) for code, quantity in components.items()}
        boms = self.env["mrp.bom"].sudo().with_context(active_test=False).search(
            [("product_id", "=", product.id), ("type", "=", "normal"), ("company_id", "in", [False, company.id])],
        )
        exact = boms.filtered(
            lambda bom: bom.product_qty == 1
            and {line.product_id.id: line.product_qty for line in bom.bom_line_ids} == expected
        )
        if len(exact) == 1:
            if boms - exact:
                raise UserError(
                    f"Product {product.display_name} has additional conflicting "
                    "historical BoMs."
                )
            return exact
        if boms:
            raise UserError(f"Product {product.display_name} has a conflicting historical BoM.")
        return self.env["mrp.bom"].sudo().with_context(**self._ctx()).create(
            {
                "product_tmpl_id": product.product_tmpl_id.id,
                "product_id": product.id,
                "product_qty": 1,
                "uom_id": product.uom_id.id,
                "type": "normal",
                "company_id": company.id,
                "bom_line_ids": [
                    Command.create(
                        {
                            "product_id": self._product(code).id,
                            "product_qty": float(quantity),
                            "uom_id": self._product(code).uom_id.id,
                        },
                    )
                    for code, quantity in components.items()
                ],
            },
        )

    def _production(self, company, warehouse, order, line, components, disposition):
        key = f"production:{order.canonical_key}:{line.line_key}"
        per_unit = {
            code: quantity / Decimal(str(line.quantity))
            for code, quantity in components.items()
        }
        existing = self.env["mrp.production"].sudo().search(
            [("company_id", "=", company.id), ("usl_b2c_source_key", "=", key)], limit=1,
        )
        if existing:
            bom = self._ensure_finished_bom(company, line.product_id, per_unit)
            expected_state = "confirmed" if disposition == "reserved" else "done"
            expected = {
                "company_id": company,
                "product_id": line.product_id,
                "product_qty": line.quantity,
                "uom_id": line.product_id.uom_id,
                "bom_id": bom,
                "date_start": order.order_date,
                "origin": order.sale_order_id.name,
                "usl_b2c_order_line_id": line,
                "usl_b2c_source_key": key,
                "state": expected_state,
            }
            if expected_state == "done":
                expected["date_finished"] = order.order_date
            self._assert_values(
                existing,
                expected,
                f"Historical production {key}",
            )
            return existing
        bom = self._ensure_finished_bom(company, line.product_id, per_unit)
        production = self.env["mrp.production"].sudo().with_context(**self._ctx()).create(
            {
                "product_id": line.product_id.id,
                "product_qty": line.quantity,
                "uom_id": line.product_id.uom_id.id,
                "bom_id": bom.id,
                "company_id": company.id,
                "date_start": order.order_date,
                "date_finished": order.order_date,
                "origin": order.sale_order_id.name,
                "usl_b2c_order_line_id": line.id,
                "usl_b2c_source_key": key,
            },
        )
        production.action_confirm()
        production.action_assign()
        if disposition != "reserved":
            production.qty_producing = line.quantity
            production._set_qty_producing()
            result = production.with_context(
                skip_backorder=True,
                skip_redirection=True,
            ).button_mark_done()
            if result is not True or production.state != "done":
                raise UserError(
                    f"Historical production {key} did not close cleanly: {result!r}.",
                )
            production.with_context(force_date=True).write(
                {"date_start": order.order_date, "date_finished": order.order_date},
            )
            (production.move_raw_ids | production.move_finished_ids).write({"date": order.order_date})
            (production.move_raw_ids | production.move_finished_ids).move_line_ids.write({"date": order.order_date})
        if set(line.production_ids.ids) != {production.id}:
            line.with_context(**self._ctx()).write(
                {"production_ids": [Command.set([production.id])]},
            )
        return production

    def _materialize_demands(self, company, warehouse, orders):
        deliveries = self.env["stock.picking"]
        productions = self.env["mrp.production"]
        for order in orders:
            sale = order.sale_order_id
            stock_lines = []
            for line in order.line_ids:
                mode = line.product_id.product_tmpl_id.b2c_fulfilment_mode
                disposition = stock_disposition(
                    order.state,
                    order.source_fulfilment_state,
                    order.external_display_id,
                    mode,
                )
                if disposition in {"pod", "cancelled"}:
                    continue
                components = source_line_components(line.original_name, line.original_variation, line.quantity)
                if components:
                    production = self._production(company, warehouse, order, line, components, disposition)
                    productions |= production
                stock_lines.append((line.product_id, Decimal(str(line.quantity)), {
                    "name": line.original_name,
                    "sale_line_id": line.sale_order_line_id.id,
                    "b2c_line_id": line.id,
                    "source_key": f"delivery:{order.canonical_key}:{line.line_key}",
                }))
            if not stock_lines:
                continue
            key = f"delivery:{order.canonical_key}"
            partner = order.shipping_partner_id
            disposition = "internal_consumption" if order.external_display_id == "1617586251" else (
                "reserved" if any(
                    stock_disposition(
                        order.state,
                        order.source_fulfilment_state,
                        order.external_display_id,
                        line.product_id.product_tmpl_id.b2c_fulfilment_mode,
                    ) == "reserved"
                    for line in order.line_ids
                    if line.product_id.product_tmpl_id.b2c_fulfilment_mode != "printful"
                ) else "delivered"
            )
            if disposition == "internal_consumption":
                inventory_location = self._inventory_loss_location(
                    company,
                    [product for product, _quantity, _extra in stock_lines],
                    key,
                )
                picking = self._done_picking(
                    company,
                    warehouse,
                    key,
                    order.order_date,
                    False,
                    stock_lines,
                    warehouse.int_type_id,
                    source_location=warehouse.lot_stock_id,
                    destination_location=inventory_location,
                )
            elif disposition == "reserved":
                picking = self._reserve_picking(
                    company, key, order.order_date, partner, stock_lines, warehouse.out_type_id, order,
                )
            else:
                picking = self._done_picking(
                    company, warehouse, key, order.fulfilment_date or order.order_date,
                    partner, stock_lines, warehouse.out_type_id,
                )
            if picking.usl_b2c_order_id != order:
                picking.with_context(**self._ctx()).write(
                    {"usl_b2c_order_id": order.id},
                )
            deliveries |= picking
            stock_line_ids = {extra["b2c_line_id"] for _product, _quantity, extra in stock_lines}
            for line in order.line_ids.filtered(lambda record: record.id in stock_line_ids):
                delivery_moves = picking.move_ids.filtered(
                    lambda move: move.usl_b2c_order_line_id == line
                )
                production_moves = (
                    line.production_ids.move_raw_ids
                    | line.production_ids.move_finished_ids
                )
                expected_moves = delivery_moves | production_moves
                if set(line.stock_move_ids.ids) != set(expected_moves.ids):
                    line.with_context(**self._ctx()).write(
                        {"stock_move_ids": [Command.set(expected_moves.ids)]},
                    )
        for event in self.env["b2c.fulfilment.event"].sudo().search([("company_id", "=", company.id)]):
            sale_lines = event.order_id.line_ids.filtered(
                lambda line: line.product_id.product_tmpl_id.b2c_fulfilment_mode == "printful",
            ).sale_order_line_id
            if sale_lines and set(event.sale_order_line_ids.ids) != set(sale_lines.ids):
                event.with_context(**self._ctx()).write(
                    {"sale_order_line_ids": [Command.set(sale_lines.ids)]},
                )
        return deliveries, productions

    def _validate_runtime_stock(self, company):
        result = {}
        for code, (expected_on_hand, expected_reserved) in EXPECTED_THEORETICAL_STOCK.items():
            product = self._product(code)
            quants = self.env["stock.quant"].sudo().search(
                [("product_id", "=", product.id), ("location_id.usage", "=", "internal"), ("company_id", "=", company.id)],
            )
            on_hand = sum(Decimal(str(value)) for value in quants.mapped("quantity"))
            reserved = sum(Decimal(str(value)) for value in quants.mapped("reserved_quantity"))
            if abs(on_hand - expected_on_hand) > Decimal("0.00001") or abs(reserved - expected_reserved) > Decimal("0.00001"):
                raise UserError(
                    f"Native stock mismatch for {code}: {on_hand}/{reserved}, "
                    f"expected {expected_on_hand}/{expected_reserved}.",
                )
            result[code] = {
                "on_hand": str(on_hand),
                "reserved": str(reserved),
                "available": str(on_hand - reserved),
            }
        return result

    def materialize(self, run, company, orders, metadata):
        apply = run.mode == "apply"
        theoretical = self._theoretical_ledger(orders, metadata)
        acquisitions = self._validate_acquisitions(company, apply)
        if not apply:
            return {
                "acquisitions_planned": len(acquisitions),
                "theoretical_stock": theoretical,
                "uses_medusa_inventory_quantities": False,
            }
        warehouse = self._warehouse(company)
        purchases = self.env["purchase.order"]
        for acquisition, partner, currency, lines in acquisitions:
            purchases |= self._purchase(run, company, warehouse, acquisition, partner, currency, lines)
        deliveries, productions = self._materialize_demands(company, warehouse, orders)
        runtime_stock = self._validate_runtime_stock(company)
        evidenced_products = self.env["product.product"]
        for code in {
            *EXPECTED_THEORETICAL_STOCK,
            *(line["code"] for acquisition in ACQUISITIONS for line in acquisition["lines"]),
        }:
            evidenced_products |= self._product(code)
        templates_to_mark = evidenced_products.product_tmpl_id.filtered(
            lambda template: template.b2c_opening_stock_state
            != "theoretical_reconstructed"
        )
        if templates_to_mark:
            templates_to_mark.write(
                {"b2c_opening_stock_state": "theoretical_reconstructed"},
            )
        return {
            "purchases": len(purchases),
            "receipts": len(
                purchases.picking_ids.filtered(
                    lambda picking: picking.state != "cancel",
                ),
            ),
            "unbuilds": self.env["mrp.unbuild"].sudo().search_count(
                [("company_id", "=", company.id), ("usl_historical_b2c", "=", True)],
            ),
            "landed_costs": self.env["stock.landed.cost"].sudo().search_count(
                [("company_id", "=", company.id), ("usl_historical_b2c", "=", True)],
            ),
            "productions": len(productions),
            "deliveries": len(
                deliveries.filtered(lambda picking: picking.picking_type_id.code == "outgoing")
            ),
            "internal_order_consumption": len(
                deliveries.filtered(lambda picking: picking.picking_type_id.code == "internal")
            ),
            "theoretical_stock": theoretical,
            "runtime_stock": runtime_stock,
            "uses_medusa_inventory_quantities": False,
        }
