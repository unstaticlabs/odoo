import os
from collections import defaultdict
from decimal import Decimal

from odoo import fields, models

from odoo.addons.usl_b2c_restore.models.relationships import (
    B2cRelationshipFinalizer,
)
from odoo.addons.usl_b2c_restore.parsers import (
    apply_etsy_refunds,
    archive_baseline,
    build_canonical_orders,
    digest,
    evidence_payload,
    normalize_printful_order_reference,
    parse_etsy_statement_events,
    parse_printful_pdf,
    parse_revolut_events,
    parse_stripe_events,
)
from odoo.addons.usl_b2c_restore.source import B2cSourceReader

RESTORE_REVISION = 4


class UslB2cRestoreRun(models.Model):
    _name = "usl.b2c.restore.run"
    _description = "USL B2C One-shot Restoration Run"
    _order = "started_at desc, id desc"

    status = fields.Selection(
        [("running", "Running"), ("passed", "Passed"), ("failed", "Failed")],
        required=True,
        default="running",
    )
    source_database = fields.Char(required=True)
    source_snapshot = fields.Char(required=True)
    started_at = fields.Datetime(required=True, default=fields.Datetime.now)
    finished_at = fields.Datetime()
    statistics_json = fields.Json(readonly=True)
    protected_before_json = fields.Json(readonly=True)
    protected_after_json = fields.Json(readonly=True)

    def _protected_fingerprint(self):
        queries = {
            "account_bank_statement_line": (
                "SELECT line.id, line.journal_id, line.partner_id, line.amount, "
                "line.amount_currency, line.foreign_currency_id, line.payment_ref, "
                "move.date FROM account_bank_statement_line AS line "
                "JOIN account_move AS move ON move.id = line.move_id ORDER BY line.id"
            ),
            "account_full_reconcile": (
                "SELECT id, create_date FROM account_full_reconcile ORDER BY id"
            ),
            "account_move": (
                "SELECT id, name, date, state, move_type, journal_id, partner_id, "
                "currency_id FROM account_move ORDER BY id"
            ),
            "account_move_line": (
                "SELECT id, move_id, journal_id, account_id, partner_id, date, debit, "
                "credit, balance, amount_currency, currency_id, analytic_distribution "
                "FROM account_move_line ORDER BY id"
            ),
            "account_payment": (
                "SELECT id, move_id, amount, currency_id, partner_id, payment_type, "
                "partner_type FROM account_payment ORDER BY id"
            ),
            "account_partial_reconcile": (
                "SELECT id, debit_move_id, credit_move_id, full_reconcile_id, amount, "
                "debit_amount_currency, credit_amount_currency "
                "FROM account_partial_reconcile ORDER BY id"
            ),
            "payment_transaction": (
                "SELECT id, reference, state, amount, currency_id, company_id "
                "FROM payment_transaction ORDER BY id"
            ),
            "purchase_order": "SELECT id, name, state, company_id FROM purchase_order ORDER BY id",
            "purchase_order_line": (
                "SELECT id, order_id, product_id, product_qty, price_unit, company_id "
                "FROM purchase_order_line ORDER BY id"
            ),
            "sale_order": "SELECT id, name, state, company_id FROM sale_order ORDER BY id",
            "sale_order_line": (
                "SELECT id, order_id, product_id, product_uom_qty, price_unit, company_id "
                "FROM sale_order_line ORDER BY id"
            ),
            "stock_move": (
                "SELECT id, product_id, state, product_uom_qty, company_id "
                "FROM stock_move ORDER BY id"
            ),
            "stock_move_line": (
                "SELECT id, move_id, product_id, quantity, company_id "
                "FROM stock_move_line ORDER BY id"
            ),
            "stock_picking": (
                "SELECT id, name, state, company_id FROM stock_picking ORDER BY id"
            ),
            "stock_quant": (
                "SELECT id, product_id, location_id, quantity, reserved_quantity, "
                "company_id FROM stock_quant ORDER BY id"
            ),
            "product_value": (
                "SELECT id, product_id, lot_id, move_id, company_id, description, "
                "value, date FROM product_value ORDER BY id"
            ),
        }
        result = {}
        for label, query in queries.items():
            self.env.cr.execute(query)
            rows = self.env.cr.fetchall()
            result[label] = {"count": len(rows), "sha256": digest(rows)}
        return result

    def _target_company(self, source_company):
        required = {"id", "name", "vat", "company_registry"}
        if set(source_company) != required or not all(source_company.values()):
            message = "Source company business identity is incomplete"
            raise RuntimeError(message)
        company = (
            self.env["res.company"]
            .sudo()
            .with_context(active_test=False)
            .search(
                [
                    ("name", "=", source_company["name"]),
                    ("partner_id.vat", "=", source_company["vat"]),
                    (
                        "partner_id.company_registry",
                        "=",
                        source_company["company_registry"],
                    ),
                ],
                limit=2,
            )
        )
        if len(company) != 1:
            raise RuntimeError(
                "Source company business identity has no unique target: "
                f"{source_company['name']} / {source_company['vat']}",
            )
        return company

    def _channels(self, company):
        result = {}
        for code, analytic_name in (
            ("direct", "Direct"),
            ("etsy", "Etsy"),
            ("medusa", "Medusa"),
        ):
            analytic = (
                self.env["account.analytic.account"]
                .sudo()
                .with_context(active_test=False, allowed_company_ids=[company.id])
                .search(
                    [
                        ("name", "=", analytic_name),
                        ("plan_id.name", "=", "Channel"),
                        ("company_id", "in", [False, company.id]),
                    ],
                    limit=2,
                )
            )
            if len(analytic) != 1:
                raise RuntimeError(
                    f"Accounting-owned Channel analytic account {analytic_name!r} "
                    "has no unique target",
                )
            channel = (
                self.env["b2c.channel"]
                .sudo()
                .with_context(active_test=False)
                .search(
                    [("company_id", "=", company.id), ("code", "=", code)],
                    limit=1,
                )
            )
            values = {
                "name": analytic_name,
                "code": code,
                "company_id": company.id,
                "analytic_account_id": analytic.id,
                "default_fulfilment_mode": "unknown",
                "active": analytic.active,
            }
            if channel:
                channel.write(values)
            else:
                channel = self.env["b2c.channel"].sudo().create(values)
            result[code] = channel
        return result

    def _currency(self, code):
        if not code:
            return self.env["res.currency"]
        currency = (
            self.env["res.currency"]
            .sudo()
            .with_context(active_test=False)
            .search([("name", "=", code.upper())], limit=2)
        )
        if len(currency) != 1:
            raise RuntimeError(f"Currency {code!r} has no unique native target")
        return currency

    def _country(self, source_value):
        raw = (source_value or "").strip()
        if not raw:
            return self.env["res.country"]
        if raw.casefold() == "the netherlands":
            raw = "NL"
        domain = [("code", "=", raw.upper())] if len(raw) == 2 else [("name", "=", raw)]
        country = self.env["res.country"].sudo().search(domain, limit=2)
        return country if len(country) == 1 else self.env["res.country"]

    def _target_attachments(self, source):
        result = {}
        for descriptor in source["files"]:
            source_file = descriptor["source"]
            attachments = (
                self.env["ir.attachment"]
                .sudo()
                .search(
                    [
                        ("name", "=", source_file.name),
                        ("checksum", "=", source_file.checksum),
                    ],
                    limit=2,
                )
            )
            result[source_file.name] = (
                attachments if len(attachments) == 1 else self.env["ir.attachment"]
            )
        return result

    def _evidence(
        self,
        company,
        descriptor,
        payload,
        *,
        schema_digest,
        row_key,
        occurred_at=None,
        contains_pii=True,
        attachment=None,
    ):
        source_file = descriptor["source"]
        payload_digest = digest([row_key, payload])
        namespace = (
            str(source_file.attachment_id)
            if source_file.attachment_id is not None
            else f"file:{descriptor['sha256']}"
        )
        key = f"{namespace}:{row_key}:{payload_digest}"
        evidence = (
            self.env["b2c.provider.evidence"]
            .sudo()
            .search(
                [("company_id", "=", company.id), ("evidence_key", "=", key)],
                limit=1,
            )
        )
        provider = {
            "etsy_items": "etsy",
            "etsy_statement": "etsy",
            "medusa": "medusa",
            "medusa_items": "medusa",
            "medusa_legacy": "medusa_legacy",
            "printful": "printful",
            "revolut": "revolut",
            "stripe_payment": "stripe",
            "stripe_payout": "stripe",
            "supporting_pdf": "other",
        }[source_file.kind]
        values = {
            "evidence_key": key,
            "company_id": company.id,
            "source_provider": provider,
            "source_name": source_file.name,
            "source_checksum": descriptor["sha256"],
            "schema_digest": schema_digest,
            "payload_digest": payload_digest,
            "payload_json": payload,
            "contains_pii": contains_pii,
            "occurred_at": occurred_at,
            "attachment_id": attachment.id if attachment else False,
        }
        target = self.env["b2c.provider.evidence"].sudo().with_context(
            b2c_evidence_import=True,
        )
        if evidence:
            evidence.with_context(b2c_evidence_import=True).write(values)
        else:
            evidence = target.create(values)
        return evidence

    @staticmethod
    def _normalized_order_state(value):
        state = (value or "").lower()
        if "refund" in state:
            return "refunded"
        if "cancel" in state:
            return "cancelled"
        if state in {"completed", "complete", "fulfilled"}:
            return "fulfilled"
        if state in {"confirmed", "paid", "captured"}:
            return "confirmed"
        if state in {"pending", "processing"}:
            return "pending"
        return "unknown"

    @staticmethod
    def _amount_conversion(company, currency, values, converted=None):
        amount_fields = (
            "subtotal",
            "shipping",
            "discount",
            "tax",
            "fee",
            "refund",
            "revenue",
            "total",
            "net",
        )
        result = {
            f"{field_name}_amount": values.get(field_name) or Decimal("0")
            for field_name in amount_fields
        }
        if currency and currency == company.currency_id:
            result.update(
                {
                    f"{field_name}_company_amount": values.get(field_name)
                    or Decimal("0")
                    for field_name in amount_fields
                },
            )
            result.update(
                {
                    "conversion_state": "not_needed",
                    "evidenced_conversion_rate": 1,
                    "conversion_evidence": "Transaction currency equals company currency.",
                },
            )
        else:
            result.update(
                {f"{field_name}_company_amount": Decimal("0") for field_name in amount_fields},
            )
            result.update(
                {
                    "conversion_state": "pending",
                    "evidenced_conversion_rate": 0,
                    "conversion_evidence": (
                        "No evidenced complete historical company-currency conversion; "
                        "company amounts intentionally remain unallocated."
                    ),
                },
            )
        if converted:
            result.update(converted)
        return result

    def _upsert_order(self, company, channels, data, attachment):
        canonical_key = f"commerce:{data['external_order_id']}"
        order = (
            self.env["b2c.order"]
            .sudo()
            .search(
                [
                    ("company_id", "=", company.id),
                    ("canonical_key", "=", canonical_key),
                ],
                limit=1,
            )
        )
        currency = self._currency(data["currency"])
        country = self._country(data["country"])
        amounts = self._amount_conversion(
            company,
            currency,
            {
                "subtotal": data["subtotal"],
                "shipping": data["shipping"],
                "discount": data["discount"],
                "tax": data["tax"],
                "fee": Decimal("0"),
                "refund": data.get("refund") or Decimal("0"),
                "revenue": data["revenue"],
                "total": data["total"],
                "net": data.get("net") or data["revenue"],
            },
        )
        values = {
            "name": f"B2C {data['external_order_id']}",
            "canonical_key": canonical_key,
            "company_id": company.id,
            "channel_id": channels[data["channel_code"]].id,
            "source_provider": data["source_provider"],
            "origin": "imported",
            "external_order_id": data["external_order_id"],
            "external_display_id": data["external_display_id"],
            "original_provider_state": data["original_provider_state"],
            "state": data["state"],
            "order_date": data["order_date"],
            "payment_date": data["payment_date"],
            "refund_date": data.get("refund_date"),
            "fulfilment_date": data["fulfilment_date"],
            "source_payment_state": data["source_payment_state"],
            "source_fulfilment_state": data["source_fulfilment_state"],
            "customer_external_id": data["customer_external_id"] or False,
            "customer_name": data["customer_name"] or False,
            "customer_email": data["customer_email"] or False,
            "shipping_name": data["shipping_name"] or False,
            "shipping_street": data["shipping_street"] or False,
            "shipping_street2": data["shipping_street2"] or False,
            "shipping_city": data["shipping_city"] or False,
            "shipping_state": data["shipping_state"] or False,
            "shipping_zip": data["shipping_zip"] or False,
            "shipping_address_raw": data["shipping_address_raw"] or False,
            "country_id": country.id if country else False,
            "original_country": data["country"],
            "currency_id": currency.id if currency else False,
            "amount_completeness": data["amount_completeness"],
            "mapping_state": "pending",
            "review_state": "pending",
            "fulfilment_mode": data["fulfilment_mode"],
            "accounting_link_state": "pending",
            "bank_link_state": "pending",
            "payment_link_state": "pending",
            "fulfilment_link_state": "pending",
            "document_link_state": "verified" if attachment else "pending",
            "supporting_attachment_id": attachment.id if attachment else False,
            **amounts,
        }
        if order:
            order.write(values)
        else:
            order = self.env["b2c.order"].sudo().create(values)
        return order

    def _supporting_link(self, company, subject_field, subject, attachment):
        if not attachment:
            return self.env["b2c.accounting.link"]
        domain = [
            ("company_id", "=", company.id),
            (subject_field, "=", subject.id),
            ("attachment_id", "=", attachment.id),
            ("link_type", "=", "supporting"),
        ]
        link = self.env["b2c.accounting.link"].sudo().search(domain, limit=1)
        values = {
            "name": f"Supporting archive: {attachment.name}",
            "company_id": company.id,
            subject_field: subject.id,
            "attachment_id": attachment.id,
            "link_type": "supporting",
            "link_state": "verified",
        }
        if link:
            link.write(values)
        else:
            link = self.env["b2c.accounting.link"].sudo().create(values)
        return link

    def restore(self, source):
        self.ensure_one()
        documents = source["documents"]
        canonical = build_canonical_orders(
            documents["etsy_items"],
            documents["medusa_legacy"][0],
            documents["medusa"][0],
            documents["medusa_items"][0],
        )
        etsy_events = parse_etsy_statement_events(documents["etsy_statement"])
        apply_etsy_refunds(canonical, etsy_events)
        stripe_events = parse_stripe_events(
            documents["stripe_payment"][0],
            documents["stripe_payout"][0],
        )
        revolut_events = parse_revolut_events(documents["revolut"][0])
        printful_descriptor = next(
            item for item in source["files"] if item["source"].kind == "printful"
        )
        printful_rows = parse_printful_pdf(printful_descriptor["content"])
        baseline = archive_baseline(
            canonical,
            documents["etsy_statement"],
            stripe_events,
            revolut_events,
            printful_rows,
            documents["stripe_payout"][0],
            source["catalog_skus"],
        )
        before = self._protected_fingerprint()
        company = self._target_company(source["source_company"])
        channels = self._channels(company)
        attachments = self._target_attachments(source)
        descriptors = {
            item["source"].name: item for item in source["files"]
        }
        evidence_cache = {}

        def row_evidence(document, row, occurred_at=None, contains_pii=True):
            cache_key = (document.name, row["_row_number"])
            if cache_key not in evidence_cache:
                evidence_cache[cache_key] = self._evidence(
                    company,
                    descriptors[document.name],
                    evidence_payload(row),
                    schema_digest=document.schema_digest,
                    row_key=str(row["_row_number"]),
                    occurred_at=occurred_at,
                    contains_pii=contains_pii,
                    attachment=attachments[document.name],
                )
            return evidence_cache[cache_key]

        sources_by_order = defaultdict(list)
        for external_id, provider, document, rows, original_id in canonical["sources"]:
            sources_by_order[external_id].append(
                (provider, document, rows, original_id),
            )

        target_orders = {}
        for external_id, order_data in sorted(canonical["orders"].items()):
            primary_attachment = self.env["ir.attachment"]
            for provider, document, _rows, _original_id in sources_by_order[external_id]:
                if provider == order_data["source_provider"]:
                    primary_attachment = attachments[document.name]
                    break
            target = self._upsert_order(
                company,
                channels,
                order_data,
                primary_attachment,
            )
            target_orders[external_id] = target
            self._supporting_link(
                company,
                "order_id",
                target,
                primary_attachment,
            )
            existing_sources = self.env["b2c.order.source"].sudo().search(
                [("order_id", "=", target.id)],
            )
            existing_sources.write({"is_primary": False})
            primary_assigned = False
            for provider, document, rows, original_id in sources_by_order[external_id]:
                evidence_records = [
                    row_evidence(
                        document,
                        row,
                        order_data["order_date"],
                        contains_pii=True,
                    )
                    for row in rows
                ]
                source_key = digest(
                    [provider, document.checksum, external_id, [row["_row_number"] for row in rows]],
                )
                source_record = self.env["b2c.order.source"].sudo().search(
                    [
                        ("company_id", "=", company.id),
                        ("source_provider", "=", provider),
                        ("source_record_key", "=", source_key),
                    ],
                    limit=1,
                )
                is_primary = (
                    not primary_assigned and provider == order_data["source_provider"]
                )
                primary_assigned = primary_assigned or is_primary
                values = {
                    "order_id": target.id,
                    "source_provider": provider,
                    "source_record_key": source_key,
                    "external_order_id": original_id,
                    "original_provider_state": order_data["original_provider_state"],
                    "source_precedence": {
                        "medusa_legacy": 10,
                        "medusa": 30,
                        "etsy": 50,
                    }[provider],
                    "is_primary": is_primary,
                    "completeness_state": (
                        "partial"
                        if descriptors[document.name]["source"].kind
                        in {"etsy_items", "medusa_items"}
                        else "header_only"
                    ),
                    "provider_payload_digest": digest(
                        [evidence.payload_digest for evidence in evidence_records],
                    ),
                    "evidence_id": evidence_records[0].id,
                }
                if source_record:
                    source_record.write(values)
                else:
                    self.env["b2c.order.source"].sudo().create(values)
            if not primary_assigned:
                raise RuntimeError(f"Canonical order {external_id} has no primary evidence")

        target_aliases = {}
        for line in canonical["lines"]:
            order = target_orders[line["external_order_id"]]
            document = line["document"]
            evidence = row_evidence(
                document,
                line["row"],
                order.order_date,
                contains_pii=True,
            )
            alias = self.env["b2c.product.alias"]
            if line["original_sku"] or line["external_listing_id"]:
                exact_product = self.env["product.product"]
                if line["original_sku"]:
                    exact_matches = (
                        self.env["product.product"]
                        .sudo()
                        .with_context(active_test=False)
                        .search(
                            [("default_code", "=", line["original_sku"])],
                            limit=2,
                        )
                    )
                    if len(exact_matches) == 1:
                        exact_product = exact_matches
                alias_domain = [
                    ("company_id", "=", company.id),
                    ("channel_id", "=", order.channel_id.id),
                    ("source_provider", "=", line["provider"]),
                    ("original_sku", "=", line["original_sku"] or False),
                    (
                        "external_listing_id",
                        "=",
                        line["external_listing_id"] or False,
                    ),
                ]
                alias = (
                    self.env["b2c.product.alias"]
                    .sudo()
                    .search(alias_domain, limit=1)
                )
                common_alias_values = {
                    "company_id": company.id,
                    "channel_id": order.channel_id.id,
                    "source_provider": line["provider"],
                    "original_sku": line["original_sku"] or False,
                    "original_name": line["original_name"],
                    "original_variation": line["original_variation"],
                    "external_listing_id": line["external_listing_id"] or False,
                    "evidence_id": evidence.id,
                }
                disposition_values = {
                    **common_alias_values,
                    "mapping_state": "verified" if exact_product else "not_applicable",
                    "product_id": exact_product.id or False,
                    "suggested_product_id": exact_product.id or False,
                    "evidence_note": (
                        "The original source SKU exactly matches one canonical "
                        "product internal reference."
                        if exact_product
                        else "No defensible exact product reference exists in the "
                        "available evidence. The original SKU, title, variation, "
                        "listing identifier and evidence are preserved without "
                        "inventing a product."
                    ),
                }
                if alias:
                    alias.write(disposition_values)
                else:
                    alias = (
                        self.env["b2c.product.alias"]
                        .sudo()
                        .create(disposition_values)
                    )
                target_aliases[alias.alias_key] = alias

            line_key = digest(
                [document.checksum, line["row"]["_row_number"], line["external_line_id"]],
            )
            target_line = self.env["b2c.order.line"].sudo().search(
                [("order_id", "=", order.id), ("line_key", "=", line_key)],
                limit=1,
            )
            currency = order.currency_id
            company_amount = (
                line["revenue"] if currency == company.currency_id else Decimal("0")
            )
            line_values = {
                "order_id": order.id,
                "line_key": line_key,
                "external_line_id": line["external_line_id"] or False,
                "external_transaction_id": line["external_transaction_id"] or False,
                "external_listing_id": line["external_listing_id"] or False,
                "original_sku": line["original_sku"] or False,
                "original_name": line["original_name"],
                "original_variation": line["original_variation"],
                "quantity": line["quantity"],
                "unit_price": line["unit_price"],
                "discount_amount": line["discount"],
                "shipping_amount": line["shipping"],
                "tax_amount": line["tax"],
                "revenue_amount": line["revenue"],
                "subtotal_amount": line["unit_price"] * line["quantity"],
                "subtotal_company_amount": company_amount,
                "discount_company_amount": (
                    line["discount"] if currency == company.currency_id else 0
                ),
                "shipping_company_amount": (
                    line["shipping"] if currency == company.currency_id else 0
                ),
                "tax_company_amount": (
                    line["tax"] if currency == company.currency_id else 0
                ),
                "revenue_company_amount": company_amount,
                "product_id": alias.product_id.id if alias else False,
                "alias_id": alias.id if alias else False,
                "mapping_state": alias.mapping_state if alias else "not_applicable",
                "amount_completeness": "partial",
                "evidence_id": evidence.id,
            }
            if target_line:
                target_line.write(line_values)
            else:
                self.env["b2c.order.line"].sudo().create(line_values)

        target_events = {}
        for provider, parsed_events in (
            ("etsy", etsy_events),
            ("stripe", stripe_events),
            ("revolut", revolut_events),
        ):
            for event in parsed_events:
                document = event["document"]
                order_reference = event.get("external_order_id") or (
                    event.get("external_session_id")
                    or event.get("external_checkout_session_id")
                    if provider == "stripe"
                    else ""
                )
                order = target_orders.get(order_reference)
                attachment = attachments[document.name]
                evidence = row_evidence(
                    document,
                    event["row"],
                    event["event_date"],
                    contains_pii=True,
                )
                currency = self._currency(event["currency"])
                same_currency = currency == company.currency_id
                company_amount = event["amount"] if same_currency else Decimal("0")
                company_refund = event["refund"] if same_currency else Decimal("0")
                company_fee = event["fee"] if same_currency else Decimal("0")
                company_net = event["net"] if same_currency else Decimal("0")
                conversion_state = "not_needed" if same_currency else "pending"
                conversion_evidence = (
                    "Transaction currency equals company currency."
                    if same_currency
                    else "Company-currency conversion is not fully evidenced."
                )
                if (
                    provider == "stripe"
                    and event.get("converted_currency") == company.currency_id.name
                    and event.get("converted_amount") is not None
                ):
                    company_amount = event["converted_amount"]
                    company_refund = event.get("converted_refund") or Decimal("0")
                    company_net = company_amount + company_refund
                    conversion_state = "processor_evidenced"
                    conversion_evidence = (
                        "Stripe exported converted amount; converted processor fee "
                        "remains unallocated."
                    )
                channel = order.channel_id if order else (
                    channels["etsy"] if provider == "etsy" else self.env["b2c.channel"]
                )
                target = self.env["b2c.payment.event"].sudo().search(
                    [
                        ("company_id", "=", company.id),
                        ("source_provider", "=", provider),
                        ("provider_event_key", "=", event["provider_event_key"]),
                    ],
                    limit=1,
                )
                values = {
                    "name": f"{provider.title()} {event['provider_event_key'][-24:]}",
                    "company_id": company.id,
                    "channel_id": channel.id if channel else False,
                    "order_id": order.id if order else False,
                    "source_provider": provider,
                    "origin": "imported",
                    "event_type": event["event_type"],
                    "provider_event_key": event["provider_event_key"],
                    "external_transaction_id": event.get("external_transaction_id") or False,
                    "external_order_id": event.get("external_order_id") or False,
                    "external_payment_intent_id": event.get("external_payment_intent_id") or False,
                    "external_session_id": event.get("external_session_id") or False,
                    "external_checkout_session_id": event.get("external_checkout_session_id") or False,
                    "external_payout_id": event.get("external_payout_id") or False,
                    "external_refund_id": event.get("external_refund_id") or False,
                    "external_original_payment_id": event.get("external_original_payment_id") or False,
                    "original_provider_state": event["original_state"],
                    "state": event["state"],
                    "event_date": event["event_date"],
                    "currency_id": currency.id,
                    "amount": event["amount"],
                    "fee_amount": event["fee"],
                    "refund_amount": event["refund"],
                    "net_amount": event["net"],
                    "company_amount": company_amount,
                    "fee_company_amount": company_fee,
                    "refund_company_amount": company_refund,
                    "net_company_amount": company_net,
                    "conversion_state": conversion_state,
                    "evidenced_conversion_rate": (
                        company_amount / event["amount"] if event["amount"] else 0
                    ),
                    "conversion_evidence": conversion_evidence,
                    "completeness_state": (
                        "complete" if same_currency else "partial"
                    ),
                    "mapping_state": "not_applicable",
                    "review_state": "pending",
                    "order_link_state": "verified" if order else "pending",
                    "accounting_link_state": "pending",
                    "bank_link_state": "pending",
                    "supporting_attachment_id": attachment.id if attachment else False,
                    "evidence_id": evidence.id,
                }
                if target:
                    target.write(values)
                else:
                    target = self.env["b2c.payment.event"].sudo().create(values)
                target_events[provider, event["provider_event_key"]] = target
                self._supporting_link(
                    company,
                    "payment_event_id",
                    target,
                    attachment,
                )

        revolut_by_external_id = {
            event.external_transaction_id: event
            for (provider, _key), event in target_events.items()
            if provider == "revolut" and event.external_transaction_id
        }
        for (provider, _key), event in target_events.items():
            if provider == "revolut" and event.external_original_payment_id:
                event.write(
                    {
                        "original_event_id": revolut_by_external_id[
                            event.external_original_payment_id
                        ].id,
                    },
                )

        printful_attachment = attachments[printful_descriptor["source"].name]
        target_fulfilments = []
        for row in printful_rows:
            external_order = normalize_printful_order_reference(row["order"])
            order = target_orders.get(external_order)
            payload = {
                key: value
                for key, value in row.items()
                if not key.startswith("_")
            }
            evidence = self._evidence(
                company,
                printful_descriptor,
                payload,
                schema_digest=digest(
                    [
                        "date",
                        "status",
                        "order",
                        "printful_id",
                        "origin_country_codes",
                        "destination",
                        "products",
                        "discount",
                        "shipping",
                        "digitalization",
                        "tax",
                        "vat",
                        "total",
                        "review",
                    ],
                ),
                row_key=str(row["_row_number"]),
                occurred_at=row["date"],
                contains_pii=False,
                attachment=printful_attachment,
            )
            key = f"printful:{row['printful_id']}:{row['_row_number']}:{row['_row_digest']}"
            target = self.env["b2c.fulfilment.event"].sudo().search(
                [
                    ("company_id", "=", company.id),
                    ("source_provider", "=", "printful"),
                    ("provider_event_key", "=", key),
                ],
                limit=1,
            )
            country = self._country(row["destination"])
            values = {
                "name": f"Printful {row['printful_id']}",
                "company_id": company.id,
                "channel_id": order.channel_id.id if order else False,
                "order_id": order.id if order else False,
                "source_provider": "printful",
                "origin": "imported",
                "provider_event_key": key,
                "external_order_id": row["order"],
                "external_printful_id": row["printful_id"],
                "original_provider_state": row["status"],
                "state": "refunded" if row["status"] == "Refunded" else "fulfilled",
                "fulfilment_mode": "printful",
                "event_date": row["date"],
                "destination_country_id": country.id if country else False,
                "origin_country_codes": row["origin_country_codes"],
                "currency_id": company.currency_id.id,
                "product_cost_amount": row["products"],
                "discount_amount": row["discount"],
                "shipping_cost_amount": row["shipping"],
                "digitalization_cost_amount": row["digitalization"],
                "tax_amount": row["tax"],
                "vat_amount": row["vat"],
                "cogs_amount": row["total"],
                "company_cogs_amount": row["total"],
                "conversion_state": "not_needed",
                "evidenced_conversion_rate": 1,
                "conversion_evidence": "Printful schedule and company currency are EUR.",
                "completeness_state": "complete",
                "review_state": "pending",
                "order_link_state": "verified" if order else "pending",
                "accounting_link_state": "pending",
                "supporting_attachment_id": (
                    printful_attachment.id if printful_attachment else False
                ),
                "evidence_id": evidence.id,
            }
            if target:
                target.write(values)
            else:
                target = self.env["b2c.fulfilment.event"].sudo().create(values)
            target_fulfilments.append(target)
            self._supporting_link(
                company,
                "fulfilment_event_id",
                target,
                printful_attachment,
            )

        for descriptor in source["files"]:
            if descriptor["source"].kind != "supporting_pdf":
                continue
            source_file = descriptor["source"]
            self._evidence(
                company,
                descriptor,
                {
                    "attachment_id": source_file.attachment_id,
                    "disposition": "Supporting business document; no table inferred.",
                    "mimetype": source_file.mimetype,
                    "size": source_file.file_size,
                },
                schema_digest=digest(["document", "no-tabular-schema"]),
                row_key="document",
                contains_pii=True,
                attachment=attachments[source_file.name],
            )

        session_scopes = set()
        for order in target_orders.values():
            session_scopes.add(
                (order.order_date.date().replace(day=1), order.source_provider),
            )
        for event in target_events.values():
            session_scopes.add(
                (event.event_date.date().replace(day=1), event.source_provider),
            )
        for event in target_fulfilments:
            session_scopes.add(
                (event.event_date.date().replace(day=1), event.source_provider),
            )
        sessions = []
        for period_start, provider in sorted(session_scopes):
            session = self.env["b2c.accounting.session"].sudo().search(
                [
                    ("company_id", "=", company.id),
                    ("period_start", "=", period_start),
                    ("channel_id", "=", False),
                    ("source_provider", "=", provider),
                ],
                limit=1,
            )
            values = {
                "company_id": company.id,
                "period_start": period_start,
                "channel_id": False,
                "source_provider": provider,
                "review_note": (
                    "Monthly provider control scope. Individual relationships remain "
                    "separate from aggregate accounting coverage."
                ),
            }
            if session:
                if session.state == "locked":
                    raise RuntimeError(f"Imported session {session.display_name} is locked")
                session.write(values)
            else:
                session = self.env["b2c.accounting.session"].sudo().create(values)
            session.action_refresh()
            sessions.append(session)

        relationship_statistics = B2cRelationshipFinalizer(
            self,
            source,
            company,
            attachments,
        ).finalize(
            require_documents=os.getenv(
                "B2C_REQUIRE_FINAL_RELATIONSHIPS",
                "0",
            )
            == "1",
        )

        after = self._protected_fingerprint()
        if before != after:
            message = (
                "B2C restoration changed protected Accounting, reconciliation, "
                "native Sales/Purchase/Payment or stock records"
            )
            raise RuntimeError(message)
        statistics = {
            "archive_baseline": baseline,
            "attachments_linked": sum(bool(value) for value in attachments.values()),
            "attachments_pending": sum(not value for value in attachments.values()),
            "channels": len(channels),
            "evidence": self.env["b2c.provider.evidence"].sudo().search_count(
                [("company_id", "=", company.id)],
            ),
            "fulfilments": len(target_fulfilments),
            "orders": len(target_orders),
            "order_lines": len(canonical["lines"]),
            "payment_events": len(target_events),
            "sessions": self.env["b2c.accounting.session"].sudo().search_count(
                [("company_id", "=", company.id)],
            ),
            "sku_aliases": len(target_aliases),
            "relationships": relationship_statistics,
        }
        self.write(
            {
                "status": "passed",
                "finished_at": fields.Datetime.now(),
                "statistics_json": statistics,
                "protected_before_json": before,
                "protected_after_json": after,
            },
        )
        return statistics


def run_restore(env):
    source = B2cSourceReader().read()
    run = env["usl.b2c.restore.run"].sudo().create(
        {
            "source_database": os.getenv(
                "B2C_SOURCE_DATABASE",
                "odoo_online_source_saas_19_3",
            ),
            "source_snapshot": os.environ["B2C_SOURCE_SNAPSHOT"],
        },
    )
    return run, run.restore(source)
