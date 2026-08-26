# ruff: noqa: EM101, F821, T201

import json

from psycopg2 import sql

MIGRATION_MODULES = {
    "usl_accounting_restore",
    "usl_b2c_restore",
    "usl_hr_restore",
    "usl_identity_restore",
    "usl_product_restore",
    "usl_project_restore",
    "usl_tese_restore",
    "usl_collaboration_restore",
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
    for model_name in env.registry.models
    for field_name in MIGRATION_COLUMNS
    if field_name in env[model_name]._fields
)
if loaded:
    raise RuntimeError(f"Migration fields remain loaded and cannot be dropped: {loaded}.")

metadata = env["ir.model.fields"].sudo().search(
    [("name", "in", sorted(MIGRATION_COLUMNS))],
)
removed_metadata = sorted(f"{field.model}.{field.name}" for field in metadata)
metadata.with_context(force_delete=True).unlink()

env.cr.execute(
    """
    SELECT schemaname, tablename, indexname
      FROM pg_indexes
     WHERE schemaname = current_schema()
       AND indexname LIKE '%_rebuild_source_identity_uniq'
     ORDER BY tablename, indexname
    """,
)
source_identity_indexes = [tuple(row) for row in env.cr.fetchall()]

removed = []
env.cr.execute(
    """
    SELECT table_name, column_name
      FROM information_schema.columns
     WHERE table_schema = current_schema()
       AND column_name = ANY(%s)
     ORDER BY table_name, column_name
    """,
    [sorted(MIGRATION_COLUMNS)],
)
for table_name, column_name in env.cr.fetchall():
    env.cr.execute(
        sql.SQL("ALTER TABLE {} DROP COLUMN {}").format(
            sql.Identifier(table_name),
            sql.Identifier(column_name),
        ),
    )
    removed.append(f"{table_name}.{column_name}")

env.cr.execute(
    """
    SELECT tablename, indexname
      FROM pg_indexes
     WHERE schemaname = current_schema()
       AND indexname LIKE '%_rebuild_source_identity_uniq'
     ORDER BY tablename, indexname
    """,
)
remaining_source_identity_indexes = [tuple(row) for row in env.cr.fetchall()]
if remaining_source_identity_indexes:
    raise RuntimeError(
        "Migration source-identity indexes remain after column finalization: "
        f"{remaining_source_identity_indexes}.",
    )

env.cr.commit()
print(
    json.dumps(
        {
            "removed_migration_columns": removed,
            "removed_migration_field_metadata": removed_metadata,
            "removed_source_identity_indexes": source_identity_indexes,
        },
        indent=2,
        sort_keys=True,
    ),
)
