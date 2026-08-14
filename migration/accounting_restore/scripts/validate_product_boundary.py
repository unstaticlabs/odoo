# ruff: noqa: EM101, F821, T201

import json

module = env["ir.module.module"].sudo().search(
    [("name", "=", "usl_accounting_restore")],
    limit=1,
)
if module and module.state not in {"uninstalled", "uninstallable"}:
    raise RuntimeError(f"Accounting migration module remains active: {module.state}.")

forbidden_models = {
    "rebuild.account.import.run",
    "rebuild.account.discrepancy",
    "rebuild.account.source.report",
    "rebuild.account.source.report.line",
    "rebuild.account.source.report.expression",
    "rebuild.account.source.report.column",
    "rebuild.account.asset",
    "rebuild.account.asset.depreciation.schedule.line",
    "rebuild.account.deferred.schedule.line",
    "rebuild.account.analytic.override",
}
loaded = forbidden_models & set(env.registry.models)
if loaded:
    raise RuntimeError(f"Migration-only models remain loaded: {sorted(loaded)}.")

source_bound_models = {
    "res.company",
    "res.partner",
    "res.currency.rate",
    "account.account",
    "account.journal",
    "account.move",
    "account.move.line",
    "account.payment",
    "account.bank.statement.line",
    "account.reconcile.model",
    "account.tax",
    "account.account.tag",
    "account.analytic.plan",
    "account.analytic.account",
    "account.analytic.line",
    "hr.employee",
    "hr.expense",
    "ir.attachment",
    "res.partner.bank",
    "res.partner.category",
    "res.partner.industry",
    "res.users",
}
forbidden_fields = {
    "rebuild_source_database",
    "rebuild_source_model",
    "rebuild_source_id",
    "rebuild_source_xmlid",
    "rebuild_source_snapshot",
    "rebuild_import_run_id",
    "rebuild_import_status",
    "rebuild_import_note",
    "rebuild_source_move_type",
    "rebuild_source_analytic_account_id",
    "rebuild_source_move_line_id",
    "rebuild_source_general_account_id",
    "rebuild_source_journal_id",
}
remaining_fields = sorted(
    (model_name, field_name)
    for model_name in source_bound_models
    for field_name in forbidden_fields
    if field_name in env[model_name]._fields
)
if remaining_fields:
    raise RuntimeError(f"Migration-only product fields remain loaded: {remaining_fields}.")

field_metadata = env["ir.model.fields"].sudo().search_count([
    ("model", "in", sorted(source_bound_models)),
    ("name", "in", sorted(forbidden_fields)),
])
if field_metadata:
    raise RuntimeError(
        f"{field_metadata} migration-only field definitions remain in metadata.",
    )

product_tables = sorted({env[model_name]._table for model_name in source_bound_models})
env.cr.execute(
    """
    SELECT table_name, column_name
      FROM information_schema.columns
     WHERE table_schema = current_schema()
       AND table_name = ANY(%s)
       AND column_name = ANY(%s)
     ORDER BY table_name, column_name
    """,
    [product_tables, sorted(forbidden_fields)],
)
physical_fields = [tuple(row) for row in env.cr.fetchall()]
if physical_fields:
    raise RuntimeError(
        f"Migration-only columns remain in the product schema: {physical_fields}.",
    )

model_metadata = env["ir.model"].sudo().search_count([
    ("model", "in", sorted(forbidden_models)),
])
if model_metadata:
    raise RuntimeError(
        f"{model_metadata} migration-only model definitions remain in metadata.",
    )

technical_xmlids = env["ir.model.data"].sudo().search_count([
    ("module", "=", "usl_accounting_restore"),
])
if technical_xmlids:
    raise RuntimeError(
        f"{technical_xmlids} migration XML IDs remain after finalization.",
    )

technical_views = env["ir.ui.view"].sudo().search_count([
    "|", "|", "|",
    ("model", "in", sorted(forbidden_models)),
    ("name", "ilike", "import run"),
    ("name", "ilike", "source trace"),
    ("arch_db", "ilike", "source snapshot"),
])
if technical_views:
    raise RuntimeError(f"{technical_views} migration-only views remain.")

for product_module_name in (
    "usl_accounting",
    "rebuild_account_migration",
):
    product_module = env["ir.module.module"].sudo().search(
        [("name", "=", product_module_name)],
        limit=1,
    )
    if not product_module or product_module.state != "installed":
        raise RuntimeError(f"Product module {product_module_name} is not installed.")

posted_lines = env["account.move.line"].sudo().search([
    ("move_id.state", "=", "posted"),
])
trip_product_codes = ["AUS26", "BCN2602", "CA26", "LPASUM26"]
trip_products = env["product.product"].sudo().with_context(active_test=False).search([
    ("default_code", "in", trip_product_codes),
])
if sorted(trip_products.mapped("default_code")) != trip_product_codes:
    raise RuntimeError("The four historical trip Products are not all present.")
if any(trip_products.product_tmpl_id.mapped("active")):
    raise RuntimeError("Historical trip Products remain active after transition.")

canada_batch = env["usl.expense.batch"].sudo().search([
    ("name", "=", "SBFH — Canada 2026"),
], limit=1)
if not canada_batch or canada_batch.expense_count != 20:
    raise RuntimeError("The final Canada Expense Batch does not contain 20 expenses.")
if canada_batch.exception_count:
    raise RuntimeError("The final Canada Expense Batch has false context exceptions.")
if any(
    expense.account_context_source != "batch"
    or expense.analytic_context_source != "batch"
    for expense in canada_batch.expense_ids
):
    raise RuntimeError("The final Canada expenses are not Batch-inherited.")

summary = {
    "migration_module_state": module.state if module else "absent",
    "migration_models_loaded": 0,
    "migration_model_metadata": model_metadata,
    "migration_fields_loaded": 0,
    "migration_field_metadata": field_metadata,
    "migration_physical_columns": len(physical_fields),
    "migration_views": technical_views,
    "migration_xmlids": technical_xmlids,
    "moves": env["account.move"].sudo().search_count([]),
    "move_lines": env["account.move.line"].sudo().search_count([]),
    "posted_debit": round(sum(posted_lines.mapped("debit")), 2),
    "posted_credit": round(sum(posted_lines.mapped("credit")), 2),
    "payments": env["account.payment"].sudo().search_count([]),
    "expenses": env["hr.expense"].sudo().search_count([]),
    "attachments": env["ir.attachment"].sudo().search_count([]),
    "expense_batch_transition": {
        "canada_expenses": canada_batch.expense_count,
        "canada_exceptions": canada_batch.exception_count,
        "trip_products_archived": len(trip_products),
    },
}
print(json.dumps(summary, indent=2, sort_keys=True))
