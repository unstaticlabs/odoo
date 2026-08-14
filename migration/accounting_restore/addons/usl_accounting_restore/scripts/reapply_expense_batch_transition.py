# ruff: noqa: EM101, F821, I001, T201

import json


run = env["rebuild.account.import.run"].sudo().search([], order="id desc", limit=1)
if not run:
    raise RuntimeError("Expense Batch transition requires an Accounting import run.")

repair = run.run_expense_batch_transition()
rerun = run.run_expense_batch_transition()
expected_codes = ["AUS26", "BCN2602", "CA26", "LPASUM26"]
trip_products = env["product.product"].sudo().with_context(active_test=False).search([
    ("default_code", "in", expected_codes),
])
canada_batch = env["usl.expense.batch"].sudo().search([
    ("name", "=", "SBFH — Canada 2026"),
], limit=1)

checks = {
    "repair_changes_only_product_activity": (
        repair["candidate_draft_count"] == 0
        and repair["reclassified_expense_count"] == 0
        and repair["created_batch_count"] == 0
        and repair["normalized_inherited_count"] == 0
        and repair["archived_trip_product_count"] == 4
        and repair["archived_trip_product_codes"] == expected_codes
    ),
    "rerun_is_noop": (
        rerun["candidate_draft_count"] == 0
        and rerun["reclassified_expense_count"] == 0
        and rerun["created_batch_count"] == 0
        and rerun["normalized_inherited_count"] == 0
        and rerun["archived_trip_product_count"] == 0
    ),
    "trip_products_archived": (
        sorted(trip_products.mapped("default_code")) == expected_codes
        and not any(trip_products.product_tmpl_id.mapped("active"))
    ),
    "canada_batch_preserved": (
        canada_batch.expense_count == 20
        and canada_batch.exception_count == 0
        and len(canada_batch.message_ids.filtered(
            lambda message: "Canada draft transition prepared" in (message.body or ""),
        )) == 1
    ),
}
if not all(checks.values()):
    raise RuntimeError(f"Expense Batch post-product repair failed: {checks}")

env.cr.commit()
print(json.dumps({
    "checks": checks,
    "repair": repair,
    "rerun": rerun,
}, indent=2, sort_keys=True))
