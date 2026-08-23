# ruff: noqa: EM101, F821, T201

import json

from psycopg2 import sql

MIGRATION_MODULE = "usl_platform_billing_restore"
PRODUCT_MODELS = {
    "usl.platform.billing.platform",
    "usl.platform.billing.session",
    "usl.platform.billing.payout",
}
MIGRATION_COLUMNS = {
    "rebuild_import_note",
    "rebuild_import_run_id",
    "rebuild_import_status",
    "rebuild_source_database",
    "rebuild_source_id",
    "rebuild_source_model",
    "rebuild_source_snapshot",
    "rebuild_source_xmlid",
}

module = env["ir.module.module"].sudo().search(
    [("name", "=", MIGRATION_MODULE)],
    limit=1,
)
if module and module.state not in {"uninstalled", "uninstallable"}:
    raise RuntimeError(
        f"{MIGRATION_MODULE} must be uninstalled before schema finalization: "
        f"{module.state}.",
    )

loaded = sorted(
    (model_name, field_name)
    for model_name in PRODUCT_MODELS
    for field_name in MIGRATION_COLUMNS
    if field_name in env[model_name]._fields
)
if loaded:
    raise RuntimeError(f"Platform migration fields remain loaded: {loaded}.")

metadata = env["ir.model.fields"].sudo().search_count(
    [
        ("model", "in", sorted(PRODUCT_MODELS)),
        ("name", "in", sorted(MIGRATION_COLUMNS)),
    ],
)
if metadata:
    raise RuntimeError(
        f"{metadata} platform migration fields remain in product metadata.",
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
print(
    json.dumps(
        {"removed_platform_migration_columns": removed},
        indent=2,
        sort_keys=True,
    ),
)
