"""Qualification gates, source metadata and the native Sales history."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from decimal import Decimal

from odoo import Command, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare

from odoo.addons.usl_b2c_restore.native_plan import (
    EXPECTED_NATIVE_COUNTS,
    source_fingerprint_mismatches,
)
from odoo.addons.usl_b2c_restore.parsers import (
    money,
    normalize_printful_order_reference,
    parse_legacy_delivery_address,
    parsed_datetime,
    quantity,
)

from .comparison import (
    _digest,
    assert_values,
    materialization_context,
    value_differs,
)


class UslB2cNativeHistoryRun(models.Model):
    _name = "usl.b2c.native.history.run"
    _description = "USL Native B2C History Materialization"
    _order = "started_at desc, id desc"

    mode = fields.Selection(
        [("dry_run", "Dry run"), ("apply", "Apply")],
        required=True,
        default="dry_run",
    )
    state = fields.Selection(
        [("running", "Running"), ("passed", "Passed"), ("failed", "Failed")],
        required=True,
        default="running",
    )
    started_at = fields.Datetime(required=True, default=fields.Datetime.now)
    finished_at = fields.Datetime(readonly=True)
    source_digest = fields.Char(readonly=True)
    report_json = fields.Json(readonly=True)
    accounting_before_json = fields.Json(readonly=True)
    accounting_after_json = fields.Json(readonly=True)

    def _ctx(self):
        return materialization_context()

    def _vendor_bill_links(self, company):
        """Return which vendor-bill lines point at a Purchase line.

        The fingerprint below deliberately excludes this column because the
        promotion writes it: a source bill is how a documented acquisition is
        evidenced.  Recording it separately keeps that single exception visible
        and bounded instead of silently outside the Accounting invariant.
        """
        self.env.cr.execute(
            """
                SELECT id, purchase_line_id
                  FROM account_move_line
                 WHERE company_id = %s AND purchase_line_id IS NOT NULL
            """,
            (company.id,),
        )
        return dict(self.env.cr.fetchall())

    def _assert_only_new_vendor_bill_links(self, company, before):
        """Prove the run only added links, and only to its own Purchase lines."""
        after = self._vendor_bill_links(company)
        stolen = sorted(
            line_id
            for line_id, purchase_line in before.items()
            if after.get(line_id) != purchase_line
        )
        if stolen:
            raise UserError(
                f"Native promotion changed existing vendor-bill relations: {stolen!r}.",
            )
        added = {
            line_id: purchase_line
            for line_id, purchase_line in after.items()
            if line_id not in before
        }
        foreign = self.env["purchase.order.line"].browse(
            set(added.values()),
        ).filtered(lambda line: not line.order_id.usl_historical_b2c)
        if foreign:
            raise UserError(
                "Native promotion linked vendor-bill lines to Purchase orders "
                f"outside the reconstructed history: {foreign.ids!r}.",
            )
        return len(added)

    def _accounting_fingerprint(self, company):
        queries = {
            "moves": """
                SELECT id, name, date, state, move_type, journal_id, partner_id,
                       currency_id, amount_total, amount_residual
                  FROM account_move WHERE company_id = %s ORDER BY id
            """,
            "lines": """
                SELECT id, move_id, account_id, debit, credit, balance,
                       amount_currency, currency_id, reconciled,
                       analytic_distribution, full_reconcile_id
                  FROM account_move_line WHERE company_id = %s ORDER BY id
            """,
            "partials": """
                SELECT p.id, p.debit_move_id, p.credit_move_id, p.amount,
                       p.debit_amount_currency, p.credit_amount_currency
                  FROM account_partial_reconcile p
                  JOIN account_move_line l ON l.id = p.debit_move_id
                 WHERE l.company_id = %s ORDER BY p.id
            """,
            "fulls": """
                SELECT f.id
                  FROM account_full_reconcile f
                 WHERE EXISTS (
                       SELECT 1 FROM account_move_line l
                        WHERE l.full_reconcile_id = f.id AND l.company_id = %s
                 ) ORDER BY f.id
            """,
            "payments": """
                SELECT id, move_id, amount, currency_id, partner_id,
                       payment_type, partner_type
                  FROM account_payment WHERE company_id = %s ORDER BY id
            """,
        }
        result = {}
        for key, query in queries.items():
            self.env.cr.execute(query, (company.id,))
            rows = self.env.cr.fetchall()
            result[key] = {"count": len(rows), "digest": _digest(rows)}
        return result

    # A database identifier says nothing about the evidence and differs between
    # a clone and production, so the fingerprint names every record by its own
    # business identity instead.
    _BUSINESS_KEYS = {
        "b2c.order": "canonical_key",
        "b2c.order.line": "line_key",
        "b2c.order.source": "source_record_key",
        "b2c.product.alias": "alias_key",
        "b2c.provider.evidence": "evidence_key",
        "b2c.fulfilment.event": "provider_event_key",
        "b2c.channel": "code",
        "product.product": "default_code",
        "product.template": "default_code",
        "res.country": "code",
        "res.currency": "name",
        "res.partner": "name",
        "ir.attachment": "checksum",
    }

    def _business_identity(self, record):
        if not record:
            return False
        key = self._BUSINESS_KEYS.get(record._name)
        if key is None:
            raise UserError(
                f"The B2C fingerprint has no business identity for {record._name}.",
            )
        return record[key] or False

    def _fingerprint_row(self, record, field_names):
        """Return one record as content, with no database identifier in it."""
        row = {"identity": self._business_identity(record)}
        for name in field_names:
            field = record._fields[name]
            value = record[name]
            if field.type == "many2one":
                row[name] = self._business_identity(value)
            elif field.type in {"many2many", "one2many"}:
                row[name] = sorted(
                    str(self._business_identity(item)) for item in value
                )
            else:
                row[name] = value
        return row

    def _source_fingerprint(self, company):
        models_and_fields = {
            "orders": (
                "b2c.order",
                [
                    "canonical_key",
                    "channel_id",
                    "source_provider",
                    "origin",
                    "external_order_id",
                    "external_display_id",
                    "order_date",
                ],
            ),
            "lines": (
                "b2c.order.line",
                [
                    "order_id",
                    "line_key",
                    "sequence",
                    "external_line_id",
                    "external_transaction_id",
                    "external_listing_id",
                    "original_sku",
                    "original_name",
                    "original_variation",
                    "quantity",
                    "unit_price",
                    "revenue_amount",
                    "product_id",
                    "alias_id",
                    "mapping_state",
                    "evidence_id",
                ],
            ),
            "aliases": (
                "b2c.product.alias",
                [
                    "channel_id",
                    "source_provider",
                    "original_sku",
                    "source_sku_is_unique",
                    "original_name",
                    "original_variation",
                    "external_listing_id",
                    "alias_key",
                    "mapping_state",
                    "product_id",
                    "evidence_id",
                ],
            ),
            "order_sources": (
                "b2c.order.source",
                [
                    "order_id",
                    "source_provider",
                    "origin",
                    "source_record_key",
                    "source_precedence",
                    "is_primary",
                    "provider_payload_digest",
                    "evidence_id",
                ],
            ),
            "fulfilment_events": (
                "b2c.fulfilment.event",
                [
                    "name",
                    "source_provider",
                    "origin",
                    "provider_event_key",
                    "external_order_id",
                    "external_fulfilment_id",
                    "external_printful_id",
                    "original_provider_state",
                    "state",
                    "fulfilment_mode",
                    "event_date",
                    "destination_country_id",
                    "origin_country_codes",
                    "currency_id",
                    "product_cost_amount",
                    "discount_amount",
                    "shipping_cost_amount",
                    "digitalization_cost_amount",
                    "tax_amount",
                    "vat_amount",
                    "cogs_amount",
                    "company_cogs_amount",
                    "conversion_state",
                    "evidenced_conversion_rate",
                    "conversion_evidence",
                    "completeness_state",
                    "review_state",
                    "evidence_id",
                ],
            ),
            "evidence": (
                "b2c.provider.evidence",
                [
                    "evidence_key",
                    "source_provider",
                    "source_name",
                    "source_checksum",
                    "schema_digest",
                    "payload_digest",
                    "payload_json",
                    "contains_pii",
                    "occurred_at",
                    "attachment_id",
                ],
            ),
        }
        result = {}
        domain = [("company_id", "=", company.id)]
        for key, (model, field_names) in models_and_fields.items():
            records = (
                self.env[model]
                .sudo()
                .with_context(active_test=False)
                .search(domain, order="id")
            )
            rows = sorted(
                (self._fingerprint_row(record, field_names) for record in records),
                key=lambda row: _digest(row),
            )
            result[key] = {"count": len(records), "digest": _digest(rows)}
        documents = (
            self.env["b2c.provider.evidence"]
            .sudo()
            .search(domain=[("company_id", "=", company.id)])
            .mapped("source_name")
        )
        result["source_documents"] = {"count": len(set(documents)), "digest": _digest(sorted(set(documents)))}
        return result

    def _company(self):
        companies = self.env["res.company"].sudo().search(
            [("partner_id.vat", "=", "FR48983982950")],
            limit=2,
        )
        if len(companies) != 1:
            raise UserError("USL company identity is not unique.")
        return companies

    def _validate_source(self, company):
        orders = self.env["b2c.order"].sudo().search([("company_id", "=", company.id)])
        lines = orders.line_ids
        providers = Counter(orders.mapped("source_provider"))
        expected_providers = {
            "etsy": EXPECTED_NATIVE_COUNTS["etsy_orders"],
            "medusa": EXPECTED_NATIVE_COUNTS["medusa_orders"],
            "medusa_legacy": EXPECTED_NATIVE_COUNTS["legacy_orders"],
        }
        if len(orders) != EXPECTED_NATIVE_COUNTS["orders"] or len(lines) != EXPECTED_NATIVE_COUNTS["detailed_lines"]:
            raise UserError(
                f"B2C source changed: {len(orders)} orders/{len(lines)} lines, expected "
                f"{EXPECTED_NATIVE_COUNTS['orders']}/{EXPECTED_NATIVE_COUNTS['detailed_lines']}.",
            )
        if dict(providers) != expected_providers:
            raise UserError(f"B2C provider order counts changed: {dict(providers)!r}.")
        purposes = Counter(orders.mapped("business_purpose"))
        if dict(purposes) != EXPECTED_NATIVE_COUNTS["business_purposes"]:
            raise UserError(
                f"B2C order purposes changed: {dict(purposes)!r}, expected "
                f"{EXPECTED_NATIVE_COUNTS['business_purposes']!r}.",
            )
        unmapped = lines.filtered(lambda line: not line.product_id or line.mapping_state != "verified")
        if unmapped:
            sample = ", ".join(f"{line.order_id.external_order_id}:{line.original_name}" for line in unmapped[:10])
            raise UserError(
                f"All {EXPECTED_NATIVE_COUNTS['detailed_lines']} detailed lines must "
                f"have exact variant mappings. Unmapped: {sample}",
            )
        printful = self.env["b2c.fulfilment.event"].sudo().search(
            [("company_id", "=", company.id), ("source_provider", "=", "printful")],
        )
        if len(printful) != EXPECTED_NATIVE_COUNTS["printful_events"]:
            raise UserError(f"Printful source changed: {len(printful)} events, expected 261.")
        source_counts = {
            "aliases": self.env["b2c.product.alias"].sudo().search_count(
                [("company_id", "=", company.id)],
            ),
            "provider_evidence": self.env["b2c.provider.evidence"].sudo().search_count(
                [("company_id", "=", company.id)],
            ),
            "order_sources": self.env["b2c.order.source"].sudo().search_count(
                [("company_id", "=", company.id)],
            ),
            "source_documents": len(
                set(
                    self.env["b2c.provider.evidence"].sudo().search(
                        [("company_id", "=", company.id)],
                    ).mapped("source_name")
                ),
            ),
        }
        source_mismatches = {
            key: {"actual": value, "expected": EXPECTED_NATIVE_COUNTS[key]}
            for key, value in source_counts.items()
            if value != EXPECTED_NATIVE_COUNTS[key]
        }
        if source_mismatches:
            raise UserError(
                f"B2C source evidence counts changed: {source_mismatches!r}.",
            )
        # Only a customer sale becomes native Sales history. Marketing,
        # prototyping and internal consumption are cost: their evidence stays,
        # and the supplier bill already carries the money.
        sales = orders.filtered(lambda order: order.business_purpose == "sale")
        return sales.sorted(lambda order: (order.order_date, order.id))

    @staticmethod
    def _joined_address(*parts):
        return ", ".join(value.strip() for value in parts if value and value.strip())

    def _order_metadata_from_evidence(self, company, order):
        primary = order.source_record_ids.filtered("is_primary")[:1]
        if not primary or not primary.evidence_id.payload_json:
            raise UserError(f"Order {order.external_order_id} has no primary source payload.")
        header = primary.evidence_id.payload_json
        line_payloads = [
            line.evidence_id.payload_json
            for line in order.line_ids.sorted("sequence")
            if line.evidence_id and line.evidence_id.payload_json
        ]
        values = {}
        if order.source_provider == "medusa_legacy":
            address = parse_legacy_delivery_address(header.get("Address"))
            country_code = address.pop("country", "")
            country = self.env["res.country"].sudo().search(
                [("code", "=", country_code)],
                limit=2,
            )
            if country_code and len(country) != 1:
                raise UserError(
                    f"Legacy order {order.external_order_id} has unknown country "
                    f"{country_code!r}.",
                )
            values = {
                **address,
                "state": "cancelled" if "cancel" in (header.get("Status") or "").casefold() else "fulfilled",
                "source_payment_state": "unavailable",
                "source_fulfilment_state": header.get("Status") or "",
                "fulfilment_date": parsed_datetime(header.get("Date")),
                "country_id": country.id or False,
                "original_country": country_code or False,
                "currency_id": self.env.ref("base.EUR").id,
            }
        elif order.source_provider == "medusa":
            payment_state = (header.get("Payment Status") or "").strip()
            fulfilment_state = (header.get("Fulfillment Status") or "").strip()
            normalized_fulfilment = fulfilment_state.casefold()
            first_name = (header.get("Customer First name") or "").strip()
            last_name = (header.get("Customer Last name") or "").strip()
            name = " ".join(value for value in (first_name, last_name) if value)
            order_date = parsed_datetime(header.get("Date"))
            state = {
                "delivered": "fulfilled",
                "partially_delivered": "partially_fulfilled",
                "not_fulfilled": (
                    "cancelled" if payment_state.casefold() == "canceled" else "confirmed"
                ),
            }.get(normalized_fulfilment, "unknown")
            country_code = (header.get("Shipping Country Code") or "").strip().upper()
            country = self.env["res.country"].sudo().search(
                [("code", "=", country_code)],
                limit=2,
            )
            if not country_code or len(country) != 1:
                raise UserError(
                    f"Medusa order {order.external_order_id} has no unique country "
                    f"for {country_code!r}.",
                )
            currency_name = (header.get("Currency Code") or "").strip().upper()
            currency = self.env["res.currency"].sudo().with_context(active_test=False).search(
                [("name", "=", currency_name)],
                limit=2,
            )
            if not currency_name or len(currency) != 1:
                raise UserError(
                    f"Medusa order {order.external_order_id} has no unique currency "
                    f"for {currency_name!r}.",
                )
            values = {
                "state": state,
                "source_payment_state": payment_state,
                "source_fulfilment_state": fulfilment_state,
                "payment_date": order_date if payment_state.casefold() == "captured" else False,
                "fulfilment_date": order_date if normalized_fulfilment in {"delivered", "partially_delivered"} else False,
                "customer_external_id": (header.get("Customer ID") or "").strip() or False,
                "customer_name": name or False,
                "customer_email": (header.get("Customer Email") or "").strip() or False,
                "shipping_name": name or False,
                "shipping_street": (header.get("Shipping Address 1") or "").strip() or False,
                "shipping_street2": (header.get("Shipping Address 2") or "").strip() or False,
                "shipping_city": (header.get("Shipping City") or "").strip() or False,
                "shipping_state": (header.get("Shipping Region ID") or "").strip() or False,
                "shipping_zip": (header.get("Shipping Postal Code") or "").strip() or False,
                "shipping_address_raw": self._joined_address(
                    name,
                    header.get("Shipping Address 1"),
                    header.get("Shipping Address 2"),
                    header.get("Shipping City"),
                    header.get("Shipping Region ID"),
                    header.get("Shipping Postal Code"),
                    country_code,
                ),
                "country_id": country.id or False,
                "original_country": country_code,
                "currency_id": currency.id or False,
                "subtotal_amount": money(header.get("Subtotal"), default=Decimal("0")),
                "shipping_amount": money(header.get("Shipping Total"), default=Decimal("0")),
                "discount_amount": -abs(money(header.get("Discount Total"), default=Decimal("0"))),
                "tax_amount": money(header.get("Tax Total"), default=Decimal("0")),
                "total_amount": money(header.get("Total"), default=Decimal("0")),
                "revenue_amount": money(header.get("Total"), default=Decimal("0")),
                "net_amount": money(header.get("Total"), default=Decimal("0")),
                "amount_completeness": "partial",
            }
        elif order.source_provider == "etsy":
            if len(line_payloads) != len(order.line_ids):
                raise UserError(f"Etsy order {order.external_order_id} has incomplete line evidence.")
            currencies = {(row.get("Currency") or "").strip().upper() for row in line_payloads}
            countries = {(row.get("Ship Country") or "").strip() for row in line_payloads}
            if len(currencies) != 1 or len(countries) != 1:
                raise UserError(f"Etsy order {order.external_order_id} has inconsistent currency or country.")
            currency_name = currencies.pop()
            country_name = countries.pop()
            currency = self.env["res.currency"].sudo().with_context(active_test=False).search(
                [("name", "=", currency_name)], limit=2,
            )
            if not currency_name or len(currency) != 1:
                raise UserError(
                    f"Etsy order {order.external_order_id} has no unique currency "
                    f"for {currency_name!r}.",
                )
            country_domain = (
                [("code", "=", "NL")]
                if country_name.casefold() == "the netherlands"
                else [("code", "=", country_name.upper())]
                if len(country_name) == 2
                else [("name", "=", country_name)]
            )
            country = self.env["res.country"].sudo().search(country_domain, limit=2)
            if not country_name or len(country) != 1:
                raise UserError(
                    f"Etsy order {order.external_order_id} has no unique country "
                    f"for {country_name!r}.",
                )
            gross = sum(
                (money(row.get("Price"), default=Decimal("0")) * quantity(row.get("Quantity")) for row in line_payloads),
                Decimal("0"),
            )
            line_total = sum(
                (money(row.get("Item Total"), default=Decimal("0")) for row in line_payloads),
                Decimal("0"),
            )
            discount = -sum(
                (abs(money(row.get("Discount Amount"), default=Decimal("0"))) for row in line_payloads),
                Decimal("0"),
            )
            shipping = sum(
                (money(row.get("Order Shipping"), default=Decimal("0")) for row in line_payloads),
                Decimal("0"),
            )
            tax = sum(
                (money(row.get("Order Sales Tax"), default=Decimal("0")) for row in line_payloads),
                Decimal("0"),
            )
            paid_dates = [parsed_datetime(row["Date Paid"]) for row in line_payloads if (row.get("Date Paid") or "").strip()]
            shipped_dates = [parsed_datetime(row["Date Shipped"]) for row in line_payloads if (row.get("Date Shipped") or "").strip()]
            def only(key):
                found = {(row.get(key) or "").strip() for row in line_payloads if (row.get(key) or "").strip()}
                if len(found) > 1:
                    raise UserError(f"Etsy order {order.external_order_id} has conflicting {key} values.")
                return next(iter(found), "")
            total = line_total + discount + shipping + tax
            refunds = order.payment_event_ids.filtered(lambda event: event.event_type == "refund")
            refund_amount = sum(Decimal(str(amount)) for amount in refunds.mapped("refund_amount"))
            values = {
                "state": "partially_refunded" if refunds else "fulfilled" if shipped_dates else "confirmed",
                "source_payment_state": "paid" if paid_dates else "unavailable",
                "source_fulfilment_state": "shipped" if shipped_dates else "unavailable",
                "payment_date": min(paid_dates) if paid_dates else False,
                "fulfilment_date": max(shipped_dates) if shipped_dates else False,
                "refund_date": max(refunds.mapped("event_date")) if refunds else False,
                "customer_name": only("Buyer") or False,
                "shipping_name": only("Ship Name") or False,
                "shipping_street": only("Ship Address1") or False,
                "shipping_street2": only("Ship Address2") or False,
                "shipping_city": only("Ship City") or False,
                "shipping_state": only("Ship State") or False,
                "shipping_zip": only("Ship Zipcode") or False,
                "shipping_address_raw": self._joined_address(
                    only("Ship Name"), only("Ship Address1"), only("Ship Address2"),
                    only("Ship City"), only("Ship State"), only("Ship Zipcode"), country_name,
                ),
                "country_id": country.id or False,
                "original_country": country_name,
                "currency_id": currency.id or False,
                "subtotal_amount": gross,
                "shipping_amount": shipping,
                "discount_amount": discount,
                "tax_amount": tax,
                "refund_amount": refund_amount,
                "revenue_amount": total,
                "total_amount": total,
                "net_amount": total + refund_amount,
                "amount_completeness": "complete",
            }
        else:
            raise UserError(f"Unsupported historical Sales provider {order.source_provider!r}.")
        currency = self.env["res.currency"].browse(values.get("currency_id") or order.currency_id.id)
        if not currency:
            raise UserError(f"Order {order.external_order_id} has no deterministic currency.")
        if currency == company.currency_id:
            for name in ("subtotal", "shipping", "discount", "tax", "refund", "revenue", "total", "net"):
                transaction_field = f"{name}_amount"
                if transaction_field in values:
                    values[f"{name}_company_amount"] = values[transaction_field]
        return values

    def _refresh_source_metadata(self, company, orders, apply):
        metadata = {}
        for order in orders:
            values = self._order_metadata_from_evidence(company, order)
            metadata[order.id] = values
            if apply:
                drift = {
                    field_name: expected
                    for field_name, expected in values.items()
                    if value_differs(order, field_name, expected)
                }
                if drift:
                    order.sudo().with_context(**self._ctx()).write(drift)
        return metadata

    def _normalize_printful_links(self, company, orders, apply):
        # Provider evidence links to every canonical order, including the ones
        # that were marketing rather than a sale.
        orders = self.env["b2c.order"].sudo().search([("company_id", "=", company.id)])
        external_counts = Counter(orders.mapped("external_order_id"))
        duplicates = sorted(
            external_id
            for external_id, count in external_counts.items()
            if external_id and count != 1
        )
        if duplicates:
            raise UserError(
                f"Canonical B2C external order IDs are not unique: {duplicates[:10]!r}.",
            )
        by_external = {
            order.external_order_id: order
            for order in orders
            if order.external_order_id
        }
        events = self.env["b2c.fulfilment.event"].sudo().search(
            [("company_id", "=", company.id), ("source_provider", "=", "printful")],
        )
        unresolved = []
        for event in events:
            raw_reference = (event.evidence_id.payload_json or {}).get("order") or event.external_order_id
            normalized = normalize_printful_order_reference(raw_reference)
            order = by_external.get(normalized)
            if not order:
                unresolved.append(f"{event.id}:{raw_reference}")
                continue
            link_values = {
                "order_id": order.id,
                "channel_id": order.channel_id.id,
                "order_link_state": "verified",
            }
            link_drifted = any(
                (
                    event[field_name].id
                    if event._fields[field_name].type == "many2one"
                    else event[field_name]
                )
                != value
                for field_name, value in link_values.items()
            )
            if apply and link_drifted:
                event.sudo().with_context(**self._ctx()).write(
                    link_values,
                )
        if unresolved:
            raise UserError(f"Printful events remain unlinked: {', '.join(unresolved[:10])}")
        return len(events)

    @staticmethod
    def _normalized_identity(*parts):
        normalized = [re.sub(r"\s+", " ", (part or "").strip()).casefold() for part in parts]
        return _digest(normalized)

    def _country(self, order):
        if order.country_id:
            return order.country_id
        if (order.original_country or "").strip().casefold() == "the netherlands":
            return self.env.ref("base.nl")
        if (order.original_country or order.shipping_address_raw or "").strip():
            raise UserError(
                f"Order {order.external_order_id} has no deterministic country "
                f"mapping for {order.original_country!r}.",
            )
        return self.env["res.country"]

    def _state(self, order, country):
        raw = (order.shipping_state or "").strip()
        if not raw or not country:
            return self.env["res.country.state"]
        states = self.env["res.country.state"].sudo().search(
            ["&", ("country_id", "=", country.id), "|", ("code", "=ilike", raw), ("name", "=ilike", raw)],
            limit=2,
        )
        return states if len(states) == 1 else self.env["res.country.state"]

    def _identity(self, company, provider, role, digest, values, evidence, parent=None):
        Identity = self.env["b2c.partner.identity"].sudo().with_context(**self._ctx())
        self.env.cr.execute(
            """
                SELECT id
                  FROM b2c_partner_identity
                 WHERE company_id = %s
                   AND source_provider = %s
                   AND identity_digest = %s
                 FOR UPDATE
            """,
            (company.id, provider, digest),
        )
        identity_ids = [row[0] for row in self.env.cr.fetchall()]
        if len(identity_ids) > 1:
            raise UserError(f"Provider identity {provider}/{digest} is not unique.")
        identity = Identity.browse(identity_ids).exists()
        partner_values = {
            "name": values["name"],
            "company_id": company.id,
            "email": values.get("email") or False,
            "street": values.get("street") or False,
            "street2": values.get("street2") or False,
            "city": values.get("city") or False,
            "zip": values.get("zip") or False,
            "country_id": values.get("country_id") or False,
            "state_id": values.get("state_id") or False,
            "type": "delivery" if role == "delivery" else "contact",
            "parent_id": parent.id if parent else False,
            "customer_rank": 1,
            "usl_historical_b2c_contact": True,
            "comment": values.get("comment") or False,
        }
        if identity:
            partner = identity.partner_id
            if partner_values != {
                key: partner[key].id if hasattr(partner[key], "id") else partner[key]
                for key in partner_values
            }:
                partner.with_context(**self._ctx()).write(partner_values)
            return identity
        partner = self.env["res.partner"].sudo().with_context(**self._ctx()).create(partner_values)
        return Identity.create(
            {
                "name": values["name"],
                "company_id": company.id,
                "source_provider": provider,
                "external_customer_id": values.get("external_customer_id") or False,
                "identity_role": role,
                "identity_digest": digest,
                "partner_id": partner.id,
                "evidence_id": evidence.id if evidence else False,
            },
        )

    def _partners_for_order(self, company, order):
        evidence = order.source_record_ids.filtered("is_primary")[:1].evidence_id
        country = self._country(order)
        state = self._state(order, country)
        address = {
            "name": order.shipping_name or order.customer_name or f"Historical recipient {order.external_order_id}",
            "email": order.customer_email,
            "street": order.shipping_street,
            "street2": order.shipping_street2,
            "city": order.shipping_city,
            "zip": order.shipping_zip,
            "country_id": country.id,
            "state_id": state.id,
            "comment": order.shipping_address_raw,
        }
        address_digest = self._normalized_identity(
            address["name"], address["street"], address["street2"], address["city"],
            address["zip"], country.code if country else order.original_country,
        )
        if order.source_provider == "medusa" and order.customer_external_id:
            customer_digest = self._normalized_identity("medusa-customer", order.customer_external_id)
            customer = self._identity(
                company,
                "medusa",
                "customer",
                customer_digest,
                {
                    "name": order.customer_name or order.customer_email or f"Medusa customer {order.customer_external_id}",
                    "email": order.customer_email,
                    "external_customer_id": order.customer_external_id,
                },
                evidence,
            )
            delivery_digest = self._normalized_identity("medusa-delivery", order.customer_external_id, address_digest)
            delivery = self._identity(
                company,
                "medusa",
                "delivery",
                delivery_digest,
                {**address, "external_customer_id": order.customer_external_id},
                evidence,
                parent=customer.partner_id,
            )
            return customer, delivery
        provider = order.source_provider
        digest = self._normalized_identity(provider, "delivery", address_digest)
        delivery = self._identity(
            company,
            provider,
            "delivery",
            digest,
            address,
            evidence,
        )
        return delivery, delivery

    def _pricelist(self, company, currency):
        pricelist = self.env["product.pricelist"].sudo().search(
            [("currency_id", "=", currency.id), ("company_id", "in", [False, company.id])],
            limit=1,
        )
        if not pricelist:
            if self.mode == "dry_run":
                return self.env["product.pricelist"]
            pricelist = self.env["product.pricelist"].sudo().create(
                {"name": f"Historical B2C {currency.name}", "currency_id": currency.id, "company_id": company.id},
            )
        return pricelist

    def _order_name(self, order):
        prefix = {"etsy": "ETSY", "medusa": "MEDUSA", "medusa_legacy": "LEGACY"}[order.source_provider]
        reference = order.external_display_id or order.external_order_id
        return f"{prefix}-{reference}"

    @staticmethod
    def _historical_completed(order):
        return order.state in {
            "fulfilled",
            "partially_refunded",
            "refunded",
            "cancelled",
        } or order.source_provider == "medusa_legacy"

    def _sale_line_values(self, sale, source_line):
        gross = Decimal(str(source_line.unit_price)) * Decimal(str(source_line.quantity))
        source_total = Decimal(str(source_line.revenue_amount))
        discount = Decimal("0")
        if gross:
            discount = (gross - source_total) / gross * Decimal("100")
        return {
            "order_id": sale.id,
            "product_id": source_line.product_id.id,
            "name": source_line.original_name,
            "product_uom_qty": source_line.quantity,
            "product_uom_id": source_line.product_id.uom_id.id,
            "price_unit": source_line.unit_price,
            "discount": float(discount),
            "tax_ids": [Command.clear()],
            "usl_b2c_order_line_id": source_line.id,
            "usl_provider_line_total": source_line.revenue_amount,
        }

    def _amount_line(self, sale, name, amount, sequence, *, adjustment=False):
        if sale.currency_id.is_zero(float(amount)):
            return self.env["sale.order.line"]
        return self.env["sale.order.line"].sudo().with_context(**self._ctx()).create(
            {
                "order_id": sale.id,
                "name": name,
                "product_uom_qty": 1,
                "price_unit": float(amount),
                "tax_ids": [Command.clear()],
                "sequence": sequence,
                "usl_provider_adjustment": adjustment,
            },
        )

    def _materialize_sale(self, company, order):
        existing = self.env["sale.order"].sudo().search([("usl_b2c_order_id", "=", order.id)], limit=1)
        if existing:
            self._validate_existing_sale(company, order, existing)
            return existing
        if self.mode == "dry_run":
            return self.env["sale.order"]
        customer_identity, delivery_identity = self._partners_for_order(company, order)
        currency = order.currency_id or company.currency_id
        pricelist = self._pricelist(company, currency)
        completed = self._historical_completed(order)
        sale = self.env["sale.order"].sudo().with_context(**self._ctx()).create(
            {
                "name": self._order_name(order),
                "company_id": company.id,
                "partner_id": customer_identity.partner_id.id,
                "partner_invoice_id": customer_identity.partner_id.id,
                "partner_shipping_id": delivery_identity.partner_id.id,
                "date_order": order.order_date,
                "currency_id": currency.id,
                "pricelist_id": pricelist.id,
                "client_order_ref": order.external_order_id,
                "origin": f"B2C evidence {order.canonical_key}",
                "usl_b2c_order_id": order.id,
                "usl_historical_b2c": True,
                "usl_historical_b2c_completed": completed,
                "usl_historical_source_warning": (
                    "Header-only historical source; item detail is unavailable."
                    if order.source_provider == "medusa_legacy"
                    else False
                ),
                "usl_source_payment_state": order.source_payment_state,
                "usl_source_fulfilment_state": order.source_fulfilment_state,
                "usl_source_total": order.total_amount,
            },
        )
        if order.source_provider == "medusa_legacy":
            self._amount_line(sale, "Historical order — item detail unavailable", order.total_amount, 10)
        else:
            for sequence, line in enumerate(order.line_ids.sorted("sequence"), start=1):
                values = self._sale_line_values(sale, line)
                values["sequence"] = sequence * 10
                native_line = self.env["sale.order.line"].sudo().with_context(**self._ctx()).create(values)
                line.with_context(**self._ctx()).write({"sale_order_line_id": native_line.id})
            self._amount_line(sale, "Provider shipping", order.shipping_amount, 9000)
            self._amount_line(sale, "Provider discount", order.discount_amount, 9010)
            self._amount_line(sale, "Provider tax", order.tax_amount, 9020)
            residual = Decimal(str(order.total_amount)) - Decimal(str(sale.amount_total))
            self._amount_line(sale, "Provider-level adjustment", residual, 9990, adjustment=True)
        if float_compare(sale.amount_total, order.total_amount, precision_rounding=currency.rounding):
            raise UserError(
                f"Native Sales total mismatch for {order.external_order_id}: "
                f"{sale.amount_total} != {order.total_amount}.",
            )
        sale.with_context(**self._ctx()).write(
            {"state": "cancel" if order.state == "cancelled" else "sale"},
        )
        order.with_context(**self._ctx()).write(
            {
                "sale_order_id": sale.id,
                "partner_identity_id": delivery_identity.id,
                "partner_id": customer_identity.partner_id.id,
                "shipping_partner_id": delivery_identity.partner_id.id,
            },
        )
        if sale.message_follower_ids:
            raise UserError(f"Historical Sales order {sale.name} unexpectedly has followers.")
        return sale

    def _validate_existing_sale(self, company, order, sale):
        """Prove an idempotent rerun found the exact accepted native order."""
        currency = order.currency_id or company.currency_id
        expected = {
            "company_id": company,
            "name": self._order_name(order),
            "currency_id": currency,
            "client_order_ref": order.external_order_id,
            "origin": f"B2C evidence {order.canonical_key}",
            "date_order": order.order_date,
            "state": "cancel" if order.state == "cancelled" else "sale",
            "usl_b2c_order_id": order,
            "usl_historical_b2c": True,
            "usl_historical_b2c_completed": self._historical_completed(order),
            "usl_historical_source_warning": (
                "Header-only historical source; item detail is unavailable."
                if order.source_provider == "medusa_legacy"
                else False
            ),
            "usl_source_payment_state": order.source_payment_state,
            "usl_source_fulfilment_state": order.source_fulfilment_state,
            "usl_source_total": order.total_amount,
        }
        if order.partner_id:
            expected["partner_id"] = order.partner_id
            expected["partner_invoice_id"] = order.partner_id
        if order.shipping_partner_id:
            expected["partner_shipping_id"] = order.shipping_partner_id
        assert_values(sale, expected, f"Historical Sales order {sale.display_name}")
        if sale.invoice_ids:
            raise UserError(
                f"Historical Sales order {sale.display_name} unexpectedly has invoices.",
            )
        if sale.message_follower_ids:
            raise UserError(
                f"Historical Sales order {sale.display_name} unexpectedly has followers.",
            )
        source_lines = order.line_ids
        native_source_lines = sale.order_line.filtered("usl_b2c_order_line_id")
        if len(native_source_lines) != len(source_lines):
            raise UserError(
                f"Historical Sales order {sale.display_name} has "
                f"{len(native_source_lines)} mapped lines; expected {len(source_lines)}.",
            )
        if set(native_source_lines.usl_b2c_order_line_id.ids) != set(source_lines.ids):
            raise UserError(
                f"Historical Sales order {sale.display_name} has incorrect source-line links.",
            )
        native_by_source = {
            line.usl_b2c_order_line_id.id: line
            for line in native_source_lines
        }
        for source_line in source_lines:
            native_line = native_by_source[source_line.id]
            expected_values = self._sale_line_values(sale, source_line)
            assert_values(
                native_line,
                {
                    "product_id": source_line.product_id,
                    "product_uom_id": source_line.product_id.uom_id,
                    "product_uom_qty": source_line.quantity,
                    "price_unit": source_line.unit_price,
                    "discount": expected_values["discount"],
                    "usl_provider_line_total": source_line.revenue_amount,
                    "tax_ids": self.env["account.tax"],
                },
                f"Historical Sales line {native_line.display_name}",
            )
        extra_lines = sale.order_line - native_source_lines
        expected_amounts = {}
        if order.source_provider == "medusa_legacy":
            if not currency.is_zero(order.total_amount):
                expected_amounts["Historical order — item detail unavailable"] = (
                    Decimal(str(order.total_amount)),
                    False,
                )
        else:
            # The residual repeats how it was created: from the amounts Odoo
            # actually stored, not from exact source decimals.  A line subtotal
            # is rounded to the currency, so re-deriving it from raw evidence
            # would move the expected residual by those rounding cents.
            stored_net = sum(
                (Decimal(str(line.price_subtotal)) for line in native_source_lines),
                Decimal("0"),
            )
            for label, amount, adjustment in (
                ("Provider shipping", order.shipping_amount, False),
                ("Provider discount", order.discount_amount, False),
                ("Provider tax", order.tax_amount, False),
                (
                    "Provider-level adjustment",
                    Decimal(str(order.total_amount))
                    - stored_net
                    - Decimal(str(order.shipping_amount))
                    - Decimal(str(order.discount_amount))
                    - Decimal(str(order.tax_amount)),
                    True,
                ),
            ):
                if not currency.is_zero(float(amount)):
                    expected_amounts[label] = (Decimal(str(amount)), adjustment)
        actual_amounts = {}
        for line in extra_lines:
            if line.name in actual_amounts:
                raise UserError(
                    f"Historical Sales order {sale.display_name} has duplicate "
                    f"amount line {line.name!r}.",
                )
            actual_amounts[line.name] = line
        if set(actual_amounts) != set(expected_amounts):
            raise UserError(
                f"Historical Sales order {sale.display_name} amount lines drifted: "
                f"{sorted(actual_amounts)!r} != {sorted(expected_amounts)!r}.",
            )
        for label, (expected_amount, expected_adjustment) in expected_amounts.items():
            line = actual_amounts[label]
            assert_values(
                line,
                {
                    "product_id": self.env["product.product"],
                    "tax_ids": self.env["account.tax"],
                    "product_uom_qty": 1,
                    "price_unit": float(expected_amount),
                    "usl_provider_adjustment": expected_adjustment,
                },
                f"Historical Sales order {sale.display_name} amount line {label!r}",
            )
        if order.sale_order_id != sale:
            raise UserError(
                f"Canonical B2C order {order.display_name} lost its native Sales back-reference.",
            )
        if float_compare(
            sale.amount_total,
            order.total_amount,
            precision_rounding=currency.rounding,
        ):
            raise UserError(
                f"Native Sales total mismatch for {order.external_order_id}: "
                f"{sale.amount_total} != {order.total_amount}.",
            )

    def _validate_native_counts(self, company, sales_report, inventory_report):
        expected = EXPECTED_NATIVE_COUNTS
        actual = {
            "sales_orders": sales_report["orders"],
            "sale_lines": sales_report["lines"],
            "contacts": sales_report["contacts"],
            "partner_identities": sales_report["identities"],
            "purchases": inventory_report["purchases"],
            "receipts": inventory_report["receipts"],
            "unbuilds": inventory_report["unbuilds"],
            "landed_costs": inventory_report["landed_costs"],
            "productions": inventory_report["productions"],
            "deliveries": inventory_report["deliveries"],
            "internal_order_consumption": inventory_report["internal_order_consumption"],
            "internal_pickings": self.env["stock.picking"].sudo().search_count(
                [
                    ("company_id", "=", company.id),
                    ("usl_historical_b2c", "=", True),
                    ("picking_type_id.code", "=", "internal"),
                ],
            ),
        }
        mismatches = {
            key: {"actual": value, "expected": expected[key]}
            for key, value in actual.items()
            if value != expected[key]
        }
        production_states = Counter(
            self.env["mrp.production"].sudo().search(
                [("company_id", "=", company.id), ("usl_b2c_source_key", "!=", False)],
            ).mapped("state"),
        )
        expected_production_states = {
            "done": expected["productions_done"],
            "confirmed": expected["productions_open"],
        }
        if dict(production_states) != expected_production_states:
            mismatches["production_states"] = {
                "actual": dict(production_states),
                "expected": expected_production_states,
            }
        deliveries = self.env["stock.picking"].sudo().search(
            [
                ("company_id", "=", company.id),
                ("usl_historical_b2c", "=", True),
                ("picking_type_id.code", "=", "outgoing"),
            ],
        )
        delivery_states = Counter(
            "done" if picking.state == "done" else "open"
            for picking in deliveries
        )
        expected_delivery_states = {
            "done": expected["deliveries_done"],
            "open": expected["deliveries_open"],
        }
        if dict(delivery_states) != expected_delivery_states:
            mismatches["delivery_states"] = {
                "actual": dict(delivery_states),
                "expected": expected_delivery_states,
            }
        if mismatches:
            raise UserError(f"Native B2C reconstruction counts drifted: {mismatches!r}.")

    def _validate_native_relationships(self, company, orders):
        productions = self.env["mrp.production"].sudo().search(
            [("company_id", "=", company.id), ("usl_b2c_source_key", "!=", False)],
        )
        productions_by_line = defaultdict(lambda: self.env["mrp.production"])
        for production in productions:
            if not production.usl_b2c_order_line_id:
                raise UserError(
                    f"Historical production {production.display_name} has no source line.",
                )
            productions_by_line[production.usl_b2c_order_line_id.id] |= production
        direct_moves = self.env["stock.move"].sudo().search(
            [
                ("company_id", "=", company.id),
                ("usl_b2c_order_line_id", "!=", False),
            ],
        )
        direct_moves_by_line = defaultdict(lambda: self.env["stock.move"])
        for move in direct_moves:
            direct_moves_by_line[move.usl_b2c_order_line_id.id] |= move
        relationship_errors = []
        for line in orders.line_ids:
            expected_productions = productions_by_line[line.id]
            if set(line.production_ids.ids) != set(expected_productions.ids):
                relationship_errors.append(
                    f"line {line.id} production links {line.production_ids.ids!r} "
                    f"!= {expected_productions.ids!r}"
                )
            expected_moves = (
                direct_moves_by_line[line.id]
                | expected_productions.move_raw_ids
                | expected_productions.move_finished_ids
            )
            if set(line.stock_move_ids.ids) != set(expected_moves.ids):
                relationship_errors.append(
                    f"line {line.id} stock-move links {line.stock_move_ids.ids!r} "
                    f"!= {expected_moves.ids!r}"
                )
            if len(relationship_errors) >= 10:
                break
        if relationship_errors:
            raise UserError(
                "Native B2C evidence relationships drifted: "
                + "; ".join(relationship_errors)
            )
        events = self.env["b2c.fulfilment.event"].sudo().search(
            [("company_id", "=", company.id), ("source_provider", "=", "printful")],
        )
        for event in events:
            expected_sale_lines = event.order_id.line_ids.filtered(
                lambda line: line.product_id.product_tmpl_id.b2c_fulfilment_mode
                == "printful"
            ).sale_order_line_id
            if (
                event.order_link_state != "verified"
                or event.channel_id != event.order_id.channel_id
                or set(event.sale_order_line_ids.ids) != set(expected_sale_lines.ids)
            ):
                raise UserError(
                    f"Printful event {event.display_name} has incomplete native links.",
                )

    def _materialize_sales(self, company, orders, metadata):
        if self.mode == "dry_run":
            # Identity and total validation still runs without creating records.
            for order in orders:
                values = metadata[order.id]
                currency = self.env["res.currency"].browse(
                    values.get("currency_id") or order.currency_id.id,
                )
                if not currency:
                    raise UserError(f"Order {order.external_order_id} has no currency.")
            return {"orders_planned": len(orders), "contacts_planned": "deterministic-at-apply"}
        sales = self.env["sale.order"].sudo().with_context(**self._ctx())
        for order in orders:
            sales |= self._materialize_sale(company, order)
        return {
            "orders": len(sales),
            "lines": self.env["sale.order.line"].sudo().search_count([("order_id", "in", sales.ids)]),
            "contacts": self.env["res.partner"].sudo().search_count(
                [("company_id", "=", company.id), ("usl_historical_b2c_contact", "=", True)],
            ),
            "identities": self.env["b2c.partner.identity"].sudo().search_count([("company_id", "=", company.id)]),
        }

    def materialize(self):
        self.ensure_one()
        company = self._company()
        source_before = self._source_fingerprint(company)
        source_mismatches = source_fingerprint_mismatches(source_before)
        if source_mismatches:
            raise UserError(
                "Frozen B2C source evidence differs from the qualified clone: "
                f"{source_mismatches!r}.",
            )
        accounting_before = self._accounting_fingerprint(company)
        bill_links_before = self._vendor_bill_links(company)
        mail_before = self.env["mail.mail"].sudo().search_count([])
        followers_before = self.env["mail.followers"].sudo().search_count([])
        orders = self._validate_source(company)
        metadata = self._refresh_source_metadata(company, orders, self.mode == "apply")
        printful_links = self._normalize_printful_links(company, orders, self.mode == "apply")
        source_digest = _digest(
            [(order.canonical_key, order.write_date, order.line_ids.ids) for order in orders],
        )
        sales_report = self._materialize_sales(company, orders, metadata)
        inventory_report = self._materialize_inventory(company, orders, metadata)
        if self.mode == "apply":
            self._validate_native_counts(company, sales_report, inventory_report)
            self._validate_native_relationships(company, orders)
        source_after = self._source_fingerprint(company)
        accounting_after = self._accounting_fingerprint(company)
        mail_after = self.env["mail.mail"].sudo().search_count([])
        followers_after = self.env["mail.followers"].sudo().search_count([])
        if source_before != source_after:
            raise UserError("Native promotion changed immutable B2C source counts or identities.")
        if accounting_before != accounting_after:
            raise UserError("Native promotion changed Accounting or reconciliation data.")
        if mail_before != mail_after:
            raise UserError("Native promotion created outbound mail.")
        if followers_before != followers_after:
            raise UserError("Native promotion subscribed records or contacts to messages.")
        vendor_bill_links = self._assert_only_new_vendor_bill_links(
            company,
            bill_links_before,
        )
        report = {
            "mode": self.mode,
            "source": source_after,
            "sales": sales_report,
            "inventory": inventory_report,
            "printful_links": printful_links,
            "vendor_bill_links": vendor_bill_links,
            "mail_before": mail_before,
            "mail_after": mail_after,
            "followers_before": followers_before,
            "followers_after": followers_after,
        }
        self.write(
            {
                "state": "passed",
                "finished_at": fields.Datetime.now(),
                "source_digest": source_digest,
                "report_json": report,
                "accounting_before_json": accounting_before,
                "accounting_after_json": accounting_after,
            },
        )
        return report

    def _materialize_inventory(self, company, orders, metadata):
        return self.env["usl.b2c.native.inventory.materializer"].materialize(
            self,
            company,
            orders,
            metadata,
        )


def run_native_history(env, mode="dry_run"):
    if mode not in {"dry_run", "apply"}:
        raise ValueError(f"Unsupported native B2C materialization mode {mode!r}")
    run = env["usl.b2c.native.history.run"].sudo().create({"mode": mode})
    return run, run.materialize()
