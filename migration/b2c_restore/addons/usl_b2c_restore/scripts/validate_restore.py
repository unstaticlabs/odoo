# ruff: noqa: F821, T201

import json
import os
from decimal import Decimal
from pathlib import Path

from odoo.addons.usl_b2c_restore.models.relationships import EXPECTED_JOURNALS
from odoo.addons.usl_b2c_restore.models.restore import UslB2cRestoreRun
from odoo.addons.usl_b2c_restore.parsers import (
    archive_baseline,
    build_canonical_orders,
    parse_etsy_statement_events,
    parse_printful_pdf,
    parse_revolut_events,
    parse_stripe_events,
)
from odoo.addons.usl_b2c_restore.source import (
    B2cSourceReader,
    CANONICAL_SUPPLIER_NAMES,
    supplier_document_fingerprint,
)

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
assert source["analytic_baseline"] == {
    "analytic_line_count": 1085,
    "channel_line_count": 246,
    "channel_product_line_count": 0,
    "cost_purpose_line_count": 0,
    "epic_line_count": 116,
}
analytic_lines = env["account.analytic.line"].sudo().search(domain)
target_analytic_baseline = {
    "analytic_line_count": len(analytic_lines),
    "channel_line_count": 0,
    "channel_product_line_count": 0,
    "cost_purpose_line_count": 0,
    "epic_line_count": 0,
}
for analytic_line in analytic_lines:
    plan_names = set(
        analytic_line._get_analytic_accounts().mapped("plan_id.name"),
    )
    if "Channel" in plan_names:
        target_analytic_baseline["channel_line_count"] += 1
        if analytic_line.product_id:
            target_analytic_baseline["channel_product_line_count"] += 1
    if "Epic" in plan_names:
        target_analytic_baseline["epic_line_count"] += 1
    if "B2C Cost Purpose" in plan_names:
        target_analytic_baseline["cost_purpose_line_count"] += 1
assert target_analytic_baseline == source["analytic_baseline"]
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
    domain + [("mapping_state", "=", "verified"), ("product_id", "!=", False)],
) == 59
assert env["b2c.product.alias"].sudo().search_count(
    domain + [("mapping_state", "=", "verified")],
) == 9
assert env["b2c.product.alias"].sudo().search_count(
    domain + [("mapping_state", "=", "not_applicable")],
) == 100
assert env["b2c.product.alias"].sudo().search_count(
    domain
    + [
        ("source_provider", "=", "medusa"),
        ("suggested_product_id", "!=", False),
        ("mapping_state", "=", "verified"),
    ],
) == 9
assert env["b2c.product.alias"].sudo().search_count(
    domain + [("mapping_state", "=", "pending")],
) == 0
assert env["b2c.order.line"].sudo().search_count(
    domain + [("mapping_state", "=", "not_applicable"), ("product_id", "=", False)],
) == 398
assert env["b2c.order.line"].sudo().search_count(
    domain + [("mapping_state", "=", "pending")],
) == 0
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

env.cr.execute("SELECT count(*) FROM account_move")
assert env.cr.fetchone()[0] == 5434
env.cr.execute("SELECT count(*) FROM account_move_line")
assert env.cr.fetchone()[0] == 13024
env.cr.execute("SELECT count(*) FROM account_partial_reconcile")
assert env.cr.fetchone()[0] == 2861

journal_fingerprint = {}
for code, (expected_count, expected_total) in EXPECTED_JOURNALS.items():
    moves = env["account.move"].sudo().search(
        [
            ("company_id", "=", company.id),
            ("state", "=", "posted"),
            ("journal_id.code", "=", code),
        ],
    )
    debit = sum(Decimal(str(value)) for value in moves.line_ids.mapped("debit"))
    credit = sum(Decimal(str(value)) for value in moves.line_ids.mapped("credit"))
    journal_fingerprint[code] = {
        "count": len(moves),
        "credit": str(credit),
        "debit": str(debit),
    }
    assert len(moves) == expected_count
    assert debit == credit == expected_total

env.cr.execute(
    """
    SELECT account.code_store ->> %s AS code,
           currency.name,
           sum(line.balance),
           sum(line.amount_currency),
           sum(line.amount_residual),
           sum(line.amount_residual_currency)
      FROM account_move_line AS line
      JOIN account_move AS move ON move.id = line.move_id
      JOIN account_account AS account ON account.id = line.account_id
 LEFT JOIN res_currency AS currency ON currency.id = line.currency_id
     WHERE move.state = 'posted'
       AND account.code_store ->> %s IN (
           '511210', '511220', '511221', '511222',
           '511230', '511231', '511232'
       )
  GROUP BY account.code_store, currency.name
  ORDER BY code, currency.name
    """,
    (str(company.id), str(company.id)),
)
clearing_balances = {
    (code, currency): tuple(Decimal(str(value)) for value in amounts)
    for code, currency, *amounts in env.cr.fetchall()
}
assert clearing_balances == {
    ("511210", "EUR"): (Decimal("0"),) * 4,
    ("511220", "EUR"): (Decimal("91.32"),) * 4,
    ("511221", "GBP"): (Decimal("0"),) * 4,
    ("511222", "USD"): (Decimal("0"),) * 4,
    ("511230", "EUR"): (Decimal("0"),) * 4,
    ("511231", "GBP"): (Decimal("0"),) * 4,
    ("511232", "USD"): (Decimal("0"),) * 4,
}

session_move_links = env["b2c.accounting.link"].sudo().search_count(
    domain
    + [
        ("session_id", "!=", False),
        ("account_move_id", "!=", False),
        ("bank_statement_line_id", "=", False),
        ("link_state", "=", "verified"),
    ],
)
bank_links = env["b2c.accounting.link"].sudo().search_count(
    domain
    + [
        ("session_id", "!=", False),
        ("bank_statement_line_id", "!=", False),
        ("link_state", "=", "verified"),
    ],
)
direct_links = env["b2c.accounting.link"].sudo().search(
    domain
    + [
        ("payment_event_id", "!=", False),
        ("account_move_line_id", "!=", False),
        ("link_state", "=", "verified"),
    ],
)
assert session_move_links == 180
assert bank_links == 81
assert len(direct_links) == 14
assert len(direct_links.payment_event_id) == 10

accounting_dispositions = {}
for model in ("b2c.order", "b2c.payment.event", "b2c.fulfilment.event"):
    accounting_dispositions[model] = {
        state: env[model].sudo().search_count(
            domain + [("accounting_link_state", "=", state)],
        )
        for state in ("verified", "partial", "not_applicable", "pending")
    }
    assert accounting_dispositions[model]["pending"] == 0

assert env["product.value"].sudo().search_count([]) == 45
assert env["product.value"].sudo().search_count(
    [
        ("value", "=", 0),
        ("date", "=", "0001-01-01 00:00:00"),
        ("user_id", "=", env.ref("base.user_root").id),
        ("description", "=", "Price update from None to 0.0 by OdooBot"),
    ],
) == 0

# Product restoration proves exact parity for all 46 source templates and variants
# before its temporary module is removed.  The clean rebuilt registry also owns
# four native templates: three expense helpers and the pre-existing delivery
# product that must not be mistaken for source-catalog drift.
all_templates = env["product.template"].sudo().with_context(active_test=False)
all_variants = env["product.product"].sudo().with_context(active_test=False)
assert all_templates.search_count([]) == 50
assert all_variants.search_count([]) == 50
assert all_templates.search_count([("default_code", "=", "TRANS & ACC")]) == 1
assert all_templates.search_count([("default_code", "=", "MIL")]) == 1
assert all_templates.search_count([("default_code", "=", "EXP_GEN")]) == 1
assert all_templates.search_count([("default_code", "=", "Delivery_007")]) == 2
expected_costs = {
    "CHAIN_CM_3MM_AISI404_CNCHO10CHO": Decimal("1.833"),
    "CHAIN_CM_4MM_AISI404_CNCHO10CHO": Decimal("4.0846"),
    "CHAIN_CM_6MM_LEROYMERLIN": Decimal("9.1178"),
    "PADLOCK_MASTER_9120EUR_ASSORTED_UNALLOCATED": Decimal("3.495"),
    "PADLOCK_MASTER_9120EUR_BLACK": Decimal("4.0115"),
    "PADLOCK_BLACK": Decimal("4.47895"),
    "PADLOCK_BLUE": Decimal("4.47895"),
    "PADLOCK_BROWN": Decimal("4.47895"),
    "PADLOCK_GOLD": Decimal("4.47895"),
    "PADLOCK_GREEN": Decimal("4.47895"),
    "PADLOCK_ORANGE": Decimal("4.47895"),
    "PADLOCK_PURPLE": Decimal("4.47895"),
    "PADLOCK_RED": Decimal("4.47895"),
    "PADLOCK_QD40_UNALLOCATED_2026-05": Decimal("4.5003"),
}
actual_costs = {}
for sku, expected_cost in expected_costs.items():
    products = (
        env["product.product"]
        .sudo()
        .with_context(active_test=False)
        .search([("default_code", "=", sku)], limit=2)
    )
    assert len(products) == 1
    actual_costs[sku] = Decimal(str(products.with_company(company).standard_price))
    assert actual_costs[sku] == expected_cost

expected_categories = {
    "GBC Finished Products",
    "GBC Raw Materials",
    "GBC Resale Goods",
}
actual_categories = set(
    env["product.category"]
    .sudo()
    .with_context(lang="en_US")
    .search([("name", "in", sorted(expected_categories))])
    .mapped("name"),
)
assert actual_categories == expected_categories
expected_locations = {
    ("WH/Event Transit", "transit"),
    ("WH/Stock/Finished Goods", "internal"),
    ("WH/Stock/Quarantine", "internal"),
    ("WH/Stock/Raw Materials", "internal"),
    ("WH/Stock/Returns", "internal"),
    ("WH/Stock/WIP", "internal"),
}
locations = (
    env["stock.location"]
    .sudo()
    .with_context(active_test=False, lang="en_US")
    .search([("company_id", "in", [False, company.id])])
)
assert expected_locations.issubset(
    {(location.complete_name, location.usage) for location in locations},
)

assert set(
    env["res.partner"]
    .sudo()
    .with_context(active_test=False)
    .search([("name", "in", list(CANONICAL_SUPPLIER_NAMES))])
    .mapped("name"),
) == set(CANONICAL_SUPPLIER_NAMES)
assert source["supplier_documents"] == {
    "rows": 76,
    "digest": "6ed09604a5a896cc87699779837d1bcf",
}
target_supplier_documents = supplier_document_fingerprint(
    env.cr,
    company.id,
    canonical=True,
)
assert target_supplier_documents == source["supplier_documents"], (
    target_supplier_documents,
    source["supplier_documents"],
)
quandun = env["res.partner"].sudo().search(
    [("name", "=", "Zhejiang Quandun Import & Export Co., Ltd.")],
)
assert len(quandun) == 1
quandun_drafts = env["account.move"].sudo().search(
    [
        ("company_id", "=", company.id),
        ("partner_id", "=", quandun.id),
        ("move_type", "=", "in_invoice"),
        ("state", "=", "draft"),
    ],
    order="amount_total",
)
assert [
    (
        Decimal(str(move.amount_total)),
        Decimal(str(move.amount_residual)),
        move.payment_state,
    )
    for move in quandun_drafts
] == [
    (Decimal("78.80"), Decimal("0"), "paid"),
    (Decimal("462.80"), Decimal("0"), "paid"),
    (Decimal("1034.00"), Decimal("0"), "paid"),
]

require_documents = os.getenv("B2C_REQUIRE_FINAL_RELATIONSHIPS", "0") == "1"
document_coverage = {"required": require_documents}
if require_documents:
    source_documents = {}
    b2c_models = {
        "b2c.order",
        "b2c.payment.event",
        "b2c.fulfilment.event",
        "b2c.accounting.session",
    }
    for item in source["files"]:
        matches = env["usl.document"].sudo().search(
            [
                ("availability_state", "=", "available"),
                "|",
                ("checksum", "=", item["sha256"]),
                ("version_ids.checksum", "=", item["sha256"]),
            ],
        )
        assert len(matches) == 1
        assert matches.link_ids.filtered(
            lambda link: link.active and link.res_model in b2c_models,
        )
        source_documents[item["source"].name] = matches.id
    assert len(source_documents) == 40
    assert env["b2c.provider.evidence"].sudo().search_count(
        domain + [("archived_document_id", "!=", False)],
    ) == 2893
    assert env["b2c.order"].sudo().search_count(
        domain + [("document_link_state", "=", "verified")],
    ) == 304
    document_coverage.update(
        {"archived_files": len(source_documents), "provider_rows": 2893},
    )

report = {
    "analytic_baseline": {
        "source": source["analytic_baseline"],
        "target": target_analytic_baseline,
    },
    "archive_baseline": baseline,
    "accounting_dispositions": accounting_dispositions,
    "clearing_balances": {
        f"{code}:{currency}": [str(value) for value in values]
        for (code, currency), values in clearing_balances.items()
    },
    "document_coverage": document_coverage,
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
        "not_applicable_aliases": 100,
        "pending_aliases": 0,
        "rejected_aliases": env["b2c.product.alias"].sudo().search_count(
            domain + [("mapping_state", "=", "rejected")],
        ),
        "verified_aliases": 9,
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
    "journal_fingerprint": journal_fingerprint,
    "product_fingerprint": {
        "categories": sorted(actual_categories),
        "costs": {sku: str(value) for sku, value in actual_costs.items()},
        "locations": sorted(expected_locations),
        "product_values": 45,
        "products": 46,
    },
    "supplier_document_fingerprint": target_supplier_documents,
    "target_counts": counts,
    "unresolved": {
        "foreign_currency_events_without_complete_company_amount": env[
            "b2c.payment.event"
        ].sudo().search_count(domain + [("conversion_state", "=", "pending")]),
        "orders_without_evidenced_currency": env["b2c.order"].sudo().search_count(
            domain + [("currency_id", "=", False)],
        ),
        "unexplained_pending_accounting_records": 0,
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
