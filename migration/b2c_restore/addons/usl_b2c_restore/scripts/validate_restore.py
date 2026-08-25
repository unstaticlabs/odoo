# ruff: noqa: F821, T201

import json
import os
from decimal import Decimal
from pathlib import Path

from odoo.addons.usl_b2c_restore.models.restore import UslB2cRestoreRun
from odoo.addons.usl_b2c_restore.parsers import (
    archive_baseline,
    build_canonical_orders,
    parse_etsy_statement_events,
    parse_printful_pdf,
    parse_revolut_events,
    parse_stripe_events,
)
from odoo.addons.usl_b2c_restore.source import B2cSourceReader

source = B2cSourceReader().read()
documents = source["documents"]
canonical = build_canonical_orders(
    documents["etsy_items"],
    documents["medusa_legacy"][0],
    documents["medusa"][0],
    documents["medusa_items"][0],
)
etsy_events = parse_etsy_statement_events(documents["etsy_statement"])
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
run = env["usl.b2c.restore.run"].sudo().search([], order="id desc", limit=1)
assert run and run.status == "passed"
current_protected = UslB2cRestoreRun._protected_fingerprint(run)
assert run.protected_before_json == run.protected_after_json == current_protected

company = run._target_company(source["source_company"])
domain = [("company_id", "=", company.id)]
counts = {
    "evidence": env["b2c.provider.evidence"].sudo().search_count(domain),
    "fulfilments": env["b2c.fulfilment.event"].sudo().search_count(domain),
    "order_lines": env["b2c.order.line"].sudo().search_count(domain),
    "orders": env["b2c.order"].sudo().search_count(domain),
    "payment_events": env["b2c.payment.event"].sudo().search_count(domain),
    "sessions": env["b2c.accounting.session"].sudo().search_count(domain),
    "sku_aliases": env["b2c.product.alias"].sudo().search_count(domain),
}
assert counts == {
    key: run.statistics_json[key]
    for key in counts
}
assert counts["orders"] == 304
assert counts["order_lines"] == 457
assert counts["payment_events"] == 1346 + 149 + 8 + 318
assert counts["fulfilments"] == 261
assert counts["evidence"] == 2893

assert env["b2c.order"].sudo().search_count(
    domain + [("source_provider", "=", "etsy")],
) == 173
assert env["b2c.order"].sudo().search_count(
    domain + [("amount_completeness", "=", "header_only")],
) == 35
assert env["b2c.order"].sudo().search_count(
    domain + [("currency_id", "=", False)],
) > 0
assert env["b2c.order.line"].sudo().search_count(
    domain + [("mapping_state", "=", "pending"), ("product_id", "=", False)],
) == 457
assert env["b2c.product.alias"].sudo().search_count(
    domain + [("mapping_state", "=", "verified")],
) == 0
assert env["b2c.product.alias"].sudo().search_count(
    domain + [("mapping_state", "=", "pending")],
) == 109
assert env["b2c.product.alias"].sudo().search_count(
    domain
    + [
        ("source_provider", "=", "medusa"),
        ("suggested_product_id", "!=", False),
        ("mapping_state", "=", "pending"),
    ],
) == 9
assert env["b2c.order.line"].sudo().search_count(
    domain
    + [
        ("source_provider", "=", "medusa"),
        ("external_line_id", "=", False),
    ],
) == 222
example = env["b2c.order"].sudo().search(
    domain + [("external_display_id", "=", "1617586399")],
)
assert len(example) == 1
assert len(example.line_ids) == 2
assert Decimal(str(sum(example.line_ids.mapped("quantity")))) == Decimal("3")

stripe_domain = domain + [("source_provider", "=", "stripe")]
assert env["b2c.payment.event"].sudo().search_count(
    stripe_domain
    + [
        ("event_type", "!=", "payout"),
        ("external_transaction_id", "=", False),
    ],
) == 72
assert len(
    set(
        env["b2c.payment.event"]
        .sudo()
        .search(stripe_domain + [("external_payment_intent_id", "!=", False)])
        .mapped("external_payment_intent_id"),
    ),
) == 134
assert len(
    set(
        env["b2c.payment.event"]
        .sudo()
        .search(
            stripe_domain
            + [
                ("event_type", "!=", "payout"),
                ("external_session_id", "!=", False),
            ],
        )
        .mapped("external_session_id"),
    ),
) == 117
assert len(
    set(
        env["b2c.payment.event"]
        .sudo()
        .search(
            stripe_domain
            + [
                ("event_type", "!=", "payout"),
                ("external_checkout_session_id", "!=", False),
            ],
        )
        .mapped("external_checkout_session_id"),
    ),
) == 4

revolut_refunds = env["b2c.payment.event"].sudo().search(
    domain + [("source_provider", "=", "revolut"), ("event_type", "=", "refund")],
)
assert len(revolut_refunds) == 7
assert all(revolut_refunds.mapped("original_event_id"))
assert all(value <= 0 for value in revolut_refunds.mapped("amount"))

printful = env["b2c.fulfilment.event"].sudo().search(
    domain + [("source_provider", "=", "printful")],
)
assert Decimal(str(sum(printful.mapped("cogs_amount")))) == Decimal("10032.35")
assert Decimal(str(sum(printful.mapped("vat_amount")))) == Decimal("1153.89")
refunds = printful.filtered(lambda event: event.state == "refunded")
assert len(refunds) == 14
assert all(value <= 0 for value in refunds.mapped("cogs_amount"))

assert env["stock.move"].sudo().search_count([]) == 0
assert env["stock.move.line"].sudo().search_count([]) == 0
assert env["stock.picking"].sudo().search_count([]) == 0
assert env["stock.quant"].sudo().search_count([]) == 0

report = {
    "archive_baseline": baseline,
    "files": [
        {
            "attachment_id": item["source"].attachment_id,
            "kind": item["source"].kind,
            "mimetype": item["source"].mimetype,
            "name": item["source"].name,
            "sha1": item["source"].checksum,
            "sha256": item["sha256"],
            "target_attachment_state": (
                "verified"
                if env["ir.attachment"].sudo().search_count(
                    [
                        ("name", "=", item["source"].name),
                        ("checksum", "=", item["source"].checksum),
                    ],
                    limit=1,
                )
                else "pending"
            ),
        }
        for item in source["files"]
    ],
    "mapping": {
        "pending_aliases": env["b2c.product.alias"].sudo().search_count(
            domain + [("mapping_state", "=", "pending")],
        ),
        "rejected_aliases": env["b2c.product.alias"].sudo().search_count(
            domain + [("mapping_state", "=", "rejected")],
        ),
        "verified_aliases": 0,
        "unlinked_accounting_records": sum(
            env[model].sudo().search_count(
                domain + [("accounting_link_state", "=", "pending")],
            )
            for model in (
                "b2c.order",
                "b2c.payment.event",
                "b2c.fulfilment.event",
            )
        ),
    },
    "protected_fingerprint": current_protected,
    "target_counts": counts,
    "unresolved": {
        "foreign_currency_events_without_complete_company_amount": env[
            "b2c.payment.event"
        ].sudo().search_count(domain + [("conversion_state", "=", "pending")]),
        "orders_without_evidenced_currency": env["b2c.order"].sudo().search_count(
            domain + [("currency_id", "=", False)],
        ),
        "orders_without_verified_accounting_link": env["b2c.order"].sudo().search_count(
            domain + [("accounting_link_state", "=", "pending")],
        ),
        "physical_opening_stock": "blocking_not_evidenced",
    },
}
evidence_dir = Path(os.environ.get("B2C_EVIDENCE_DIR", "/tmp/b2c-evidence"))
evidence_dir.mkdir(parents=True, exist_ok=True)
(evidence_dir / "source-target-parity.json").write_text(
    json.dumps(report, indent=2, sort_keys=True, default=str) + "\n",
)
(evidence_dir / "mapping-and-discrepancies.json").write_text(
    json.dumps(
        {"mapping": report["mapping"], "unresolved": report["unresolved"]},
        indent=2,
        sort_keys=True,
    )
    + "\n",
)
print(json.dumps(report, indent=2, sort_keys=True))
