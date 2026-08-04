# ruff: noqa: EM101, F821, T201

import json

from psycopg2 import sql

MIGRATION_MODULES = {
    "usl_accounting_restore",
    "usl_hr_restore",
    "usl_identity_restore",
    "usl_product_restore",
    "usl_project_restore",
    "usl_tese_restore",
}
PRODUCT_MODELS = {
    "account.account",
    "account.account.tag",
    "account.analytic.account",
    "account.analytic.line",
    "account.analytic.plan",
    "account.bank.statement.line",
    "account.journal",
    "account.move",
    "account.move.line",
    "account.payment",
    "account.reconcile.model",
    "account.tax",
    "hr.employee",
    "hr.expense",
    "ir.attachment",
    "res.company",
    "res.currency.rate",
    "res.partner",
    "res.partner.bank",
    "res.partner.category",
    "res.partner.industry",
    "res.users",
}
MIGRATION_COLUMNS = {
    "rebuild_import_note",
    "rebuild_import_run_id",
    "rebuild_import_status",
    "rebuild_source_analytic_account_id",
    "rebuild_source_database",
    "rebuild_source_general_account_id",
    "rebuild_source_id",
    "rebuild_source_journal_id",
    "rebuild_source_model",
    "rebuild_source_move_line_id",
    "rebuild_source_move_type",
    "rebuild_source_snapshot",
    "rebuild_source_xmlid",
}

active_modules = env["ir.module.module"].sudo().search(
    [
        ("name", "in", sorted(MIGRATION_MODULES)),
        ("state", "not in", ["uninstalled", "uninstallable"]),
    ],
)
if active_modules:
    raise RuntimeError(
        "Migration modules must be uninstalled before schema finalization: "
        f"{sorted((module.name, module.state) for module in active_modules)}.",
    )

loaded = sorted(
    (model_name, field_name)
    for model_name in PRODUCT_MODELS
    for field_name in MIGRATION_COLUMNS
    if field_name in env[model_name]._fields
)
if loaded:
    raise RuntimeError(f"Migration fields remain loaded and cannot be dropped: {loaded}.")

metadata = env["ir.model.fields"].sudo().search_count(
    [("model", "in", sorted(PRODUCT_MODELS)), ("name", "in", sorted(MIGRATION_COLUMNS))],
)
if metadata:
    raise RuntimeError(
        f"{metadata} migration field definitions remain in product metadata.",
    )

removed = []
for model_name in sorted(PRODUCT_MODELS):
    table_name = env[model_name]._table
    env.cr.execute(
        """
        SELECT column_name
          FROM information_schema.columns
         WHERE table_schema = current_schema()
           AND table_name = %s
           AND column_name = ANY(%s)
         ORDER BY column_name
        """,
        [table_name, sorted(MIGRATION_COLUMNS)],
    )
    for (column_name,) in env.cr.fetchall():
        env.cr.execute(
            sql.SQL("ALTER TABLE {} DROP COLUMN {}").format(
                sql.Identifier(table_name),
                sql.Identifier(column_name),
            ),
        )
        removed.append(f"{table_name}.{column_name}")

env.cr.commit()
print(json.dumps({"removed_migration_columns": removed}, indent=2, sort_keys=True))
