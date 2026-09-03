from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from decimal import Decimal

from odoo import Command, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare

from odoo.addons.usl_b2c.models.native_history import MATERIALIZATION_CONTEXT
from odoo.addons.usl_b2c_restore.native_plan import (
    ACQUISITIONS,
    EXPECTED_NATIVE_COUNTS,
    EXPECTED_THEORETICAL_STOCK,
    PACK_COMPONENTS,
    source_line_components,
    stock_disposition,
)
from odoo.addons.usl_b2c_restore.parsers import (
    money,
    normalize_printful_order_reference,
    parse_legacy_delivery_address,
    parsed_datetime,
    quantity,
)


BILL_EVIDENCE_COUNTS = Counter(
    (line["bill_ref"], line["bill_label"])
    for acquisition in ACQUISITIONS
    for line in acquisition["lines"]
)


def _digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, default=str, separators=(",", ":")).encode(),
    ).hexdigest()


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
        return {
            MATERIALIZATION_CONTEXT: True,
            "tracking_disable": True,
            "mail_create_nosubscribe": True,
            "mail_notrack": True,
            "mail_notify_force_send": False,
        }

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

    def _source_fingerprint(self, company):
        models_and_domain = {
            "orders": ("b2c.order", [("company_id", "=", company.id)]),
            "lines": ("b2c.order.line", [("company_id", "=", company.id)]),
            "aliases": ("b2c.product.alias", [("company_id", "=", company.id)]),
            "evidence": ("b2c.provider.evidence", [("company_id", "=", company.id)]),
        }
        result = {}
        for key, (model, domain) in models_and_domain.items():
            records = self.env[model].sudo().with_context(active_test=False).search(domain)
            result[key] = {"count": len(records), "ids": _digest(records.ids)}
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
        unmapped = lines.filtered(lambda line: not line.product_id or line.mapping_state != "verified")
        if unmapped:
            sample = ", ".join(f"{line.order_id.external_order_id}:{line.original_name}" for line in unmapped[:10])
            raise UserError(f"All 457 detailed lines must have exact variant mappings. Unmapped: {sample}")
        printful = self.env["b2c.fulfilment.event"].sudo().search(
            [("company_id", "=", company.id), ("source_provider", "=", "printful")],
        )
        if len(printful) != EXPECTED_NATIVE_COUNTS["printful_events"]:
            raise UserError(f"Printful source changed: {len(printful)} events, expected 261.")
        return orders.sorted(lambda order: (order.order_date, order.id))

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
            country = self.env["res.country"].sudo().search([("code", "=", country_code)], limit=1)
            currency = self.env["res.currency"].sudo().with_context(active_test=False).search(
                [("name", "=", (header.get("Currency Code") or "").strip().upper())],
                limit=1,
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
                [("name", "=", currency_name)], limit=1,
            )
            country_domain = (
                [("code", "=", "NL")]
                if country_name.casefold() == "the netherlands"
                else [("code", "=", country_name.upper())]
                if len(country_name) == 2
                else [("name", "=", country_name)]
            )
            country = self.env["res.country"].sudo().search(country_domain, limit=1)
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
                order.sudo().write(values)
        return metadata

    def _normalize_printful_links(self, company, orders, apply):
        by_external = {order.external_order_id: order for order in orders}
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
            if apply and event.order_id != order:
                event.sudo().write(
                    {"order_id": order.id, "channel_id": order.channel_id.id, "order_link_state": "verified"},
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
            completed = self._historical_completed(order)
            if existing.usl_historical_b2c_completed != completed:
                existing.write({"usl_historical_b2c_completed": completed})
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
        sales = self.env["sale.order"]
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
        accounting_before = self._accounting_fingerprint(company)
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
        report = {
            "mode": self.mode,
            "source": source_after,
            "sales": sales_report,
            "inventory": inventory_report,
            "printful_links": printful_links,
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


class UslB2cNativeInventoryMaterializer(models.AbstractModel):
    _name = "usl.b2c.native.inventory.materializer"
    _description = "USL Native B2C Inventory Materializer"

    def _ctx(self):
        return {
            MATERIALIZATION_CONTEXT: True,
            "tracking_disable": True,
            "mail_create_nosubscribe": True,
            "mail_notrack": True,
            "mail_notify_force_send": False,
        }

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
            exact.active = True
            (boms - exact).active = False
            return exact
        if boms:
            boms.active = False
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

    def _purchase(self, run, company, warehouse, acquisition, partner, currency, lines):
        existing = self.env["purchase.order"].sudo().search(
            [("company_id", "=", company.id), ("usl_b2c_source_key", "=", acquisition["key"])], limit=1,
        )
        if existing:
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
        purchase.button_confirm()
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
            return existing
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
        existing = self.env["mrp.production"].sudo().search(
            [("company_id", "=", company.id), ("usl_b2c_source_key", "=", key)], limit=1,
        )
        if existing:
            return existing
        per_unit = {code: quantity / Decimal(str(line.quantity)) for code, quantity in components.items()}
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
        line.with_context(**self._ctx()).write(
            {
                "production_ids": [Command.link(production.id)],
                "stock_move_ids": [Command.set((production.move_raw_ids | production.move_finished_ids).ids)],
            },
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
            picking.write({"usl_b2c_order_id": order.id})
            deliveries |= picking
            for line in order.line_ids.filtered(lambda record: record.id in [extra[2]["b2c_line_id"] for extra in stock_lines]):
                moves = picking.move_ids.filtered(lambda move: move.usl_b2c_order_line_id == line)
                line.with_context(**self._ctx()).write({"stock_move_ids": [Command.link(move.id) for move in moves]})
        for event in self.env["b2c.fulfilment.event"].sudo().search([("company_id", "=", company.id)]):
            sale_lines = event.order_id.line_ids.filtered(
                lambda line: line.product_id.product_tmpl_id.b2c_fulfilment_mode == "printful",
            ).sale_order_line_id
            if sale_lines:
                event.with_context(**self._ctx()).write({"sale_order_line_ids": [Command.set(sale_lines.ids)]})
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
        evidenced_products.product_tmpl_id.write(
            {"b2c_opening_stock_state": "theoretical_reconstructed"},
        )
        return {
            "purchases": len(purchases),
            "receipts": len(purchases.picking_ids),
            "unbuilds": self.env["mrp.unbuild"].sudo().search_count(
                [("company_id", "=", company.id), ("usl_historical_b2c", "=", True)],
            ),
            "landed_costs": self.env["stock.landed.cost"].sudo().search_count(
                [("company_id", "=", company.id), ("usl_historical_b2c", "=", True)],
            ),
            "productions": len(productions),
            "deliveries": len(deliveries),
            "theoretical_stock": theoretical,
            "runtime_stock": runtime_stock,
            "uses_medusa_inventory_quantities": False,
        }
