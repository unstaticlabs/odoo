# ruff: noqa: F821, T201

import json

from odoo.addons.usl_platform_billing_restore.models.restore import (
    BOOTSTRAP_SHA256,
    PlatformBillingSourceReader,
    canonical_digest,
    default_source_options,
)

options = default_source_options()
source = PlatformBillingSourceReader(options).read()
run = env["usl.platform.billing.restore.run"].sudo().search(
    [],
    order="id desc",
    limit=1,
)
assert run and run.status == "passed", "Latest platform billing restore did not pass."
assert run.bootstrap_sha256 == BOOTSTRAP_SHA256
assert len(options["source_dump_sha256"]) == 64
assert options["snapshot"] == f"source-{options['source_dump_sha256'][:12]}"
assert run.source_dump_sha256 == options["source_dump_sha256"]
assert source["counts"]["platforms"] >= 4
assert source["counts"]["sessions"] >= 3
assert source["counts"]["moves"] == 51
assert run.statistics_json["source"] == source["counts"]
assert run.statistics_json["source"]["bank_candidates"] >= 0
assert run.statistics_json["target"]["bank_candidates"] == 0
assert run.statistics_json["source"]["platforms"] == run.platform_count
assert run.statistics_json["source"]["sessions"] == run.session_count
assert run.statistics_json["source"]["payouts"] == run.payout_count
assert run.statistics_json["source"]["moves"] == run.move_count
assert run.statistics_json["linked_move_count"] == 51
assert (
    run.statistics_json["ledger_digest_before"]
    == run.statistics_json["ledger_digest_after"]
)

platforms = (
    env["usl.platform.billing.platform"]
    .sudo()
    .with_context(active_test=False)
    .search([("rebuild_source_model", "=", "x_content_platform")])
)
sessions = (
    env["usl.platform.billing.session"]
    .sudo()
    .search([("rebuild_source_model", "=", "x_content_billing_session")])
)
payouts = (
    env["usl.platform.billing.payout"]
    .sudo()
    .search([("rebuild_source_model", "=", "x_content_payout_line")])
)
source_payouts = {row["id"]: row for row in source["payouts"]}
moves = (
    env["account.move"]
    .sudo()
    .search(
        [
            ("rebuild_source_model", "=", "account.move"),
            ("rebuild_source_id", "in", [row["id"] for row in source["moves"]]),
        ],
    )
)
assert len(platforms) == source["counts"]["platforms"]
assert len(sessions) == source["counts"]["sessions"]
assert len(payouts) == source["counts"]["payouts"]
assert len(moves) == 51
assert all(
    move.platform_billing_session_id
    and move.platform_billing_platform_id
    and move.platform_billing_payout_ids
    for move in moves
)
assert not env["ir.model"].sudo().search_count(
    [("model", "=", "x_content_bank_candidate")],
)
duplicate_refs = env["usl.platform.billing.payout"].sudo()._read_group(
    [
        ("rebuild_source_model", "=", "x_content_payout_line"),
        ("platform_reference", "!=", False),
    ],
    ["company_id", "platform_id", "platform_reference"],
    ["__count"],
    having=[("__count", ">", 1)],
)
assert not duplicate_refs
for payout in payouts:
    source_payout = source_payouts[payout.rebuild_source_id]
    if source_payout.get("x_bank_match_status") == "reconciled":
        assert payout.bank_match_status == "reconciled", (
            payout.platform_reference,
            payout.bank_match_status,
        )
        assert payout.platform_currency_id.compare_amounts(
            sum(payout.bank_allocation_ids.mapped("payout_amount")),
            payout.net_platform_amount,
        ) >= 0, payout.platform_reference
    if source_payout.get("x_state") == "reconciled":
        assert payout.state == "paid", (payout.platform_reference, payout.state)
application_digest = canonical_digest(
    {
        "platforms": [
            (
                record.rebuild_source_id,
                record.name,
                record.company_id.id,
                record.commission_rate,
            )
            for record in platforms.sorted("rebuild_source_id")
        ],
        "sessions": [
            (
                record.rebuild_source_id,
                record.name,
                record.period_month,
                record.state,
            )
            for record in sessions.sorted("rebuild_source_id")
        ],
        "payouts": [
            (
                record.rebuild_source_id,
                record.platform_reference,
                record.net_platform_amount,
                record.state,
                record.bank_match_status,
                tuple(
                    sorted(
                        (
                            allocation.bank_statement_line_id.id,
                            allocation.bank_amount,
                            allocation.payout_amount,
                        )
                        for allocation in record.bank_allocation_ids
                    ),
                ),
                record.customer_invoice_id.id,
                record.vendor_bill_id.id,
                record.compensation_move_id.id,
                tuple(sorted(record.bank_statement_line_ids.ids)),
            )
            for record in payouts.sorted("rebuild_source_id")
        ],
    },
)
assert application_digest == run.statistics_json["canonical_digest"]

summary = {
    "status": "passed",
    "run_id": run.id,
    "counts": run.statistics_json["target"],
    "application_digest": application_digest,
    "ledger_digest": run.statistics_json["ledger_digest_after"],
}
print(json.dumps(summary, default=str, indent=2, sort_keys=True))
