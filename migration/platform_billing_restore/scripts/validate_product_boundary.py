# ruff: noqa: F821, T201

import json

from odoo.tools import config

migration_addons_path = "/mnt/platform-billing-migration-addons"
active_addons_paths = {
    path.strip()
    for path in str(config.get("addons_path", "")).split(",")
    if path.strip()
}
if migration_addons_path in active_addons_paths:
    raise RuntimeError(
        f"Platform migration add-ons path remains active: {migration_addons_path}.",
    )

migration_module = env["ir.module.module"].sudo().search(
    [("name", "=", "usl_platform_billing_restore")],
    limit=1,
)
if migration_module and migration_module.state not in {
    "uninstalled",
    "uninstallable",
}:
    raise RuntimeError(
        f"Platform migration module remains active: {migration_module.state}.",
    )

forbidden_models = {
    "usl.platform.billing.restore.run",
    "usl.platform.billing.restore.issue",
}
loaded_models = forbidden_models & set(env.registry.models)
if loaded_models:
    raise RuntimeError(
        f"Platform migration models remain loaded: {sorted(loaded_models)}.",
    )

business_models = {
    "usl.platform.billing.platform",
    "usl.platform.billing.session",
    "usl.platform.billing.payout",
}
forbidden_fields = {
    "rebuild_source_database",
    "rebuild_source_model",
    "rebuild_source_id",
    "rebuild_source_snapshot",
    "rebuild_import_status",
    "rebuild_import_note",
}
remaining_fields = sorted(
    (model_name, field_name)
    for model_name in business_models
    for field_name in forbidden_fields
    if field_name in env[model_name]._fields
)
if remaining_fields:
    raise RuntimeError(
        f"Migration trace fields remain on product models: {remaining_fields}.",
    )
field_metadata = env["ir.model.fields"].sudo().search_count(
    [
        ("model", "in", sorted(business_models)),
        ("name", "in", sorted(forbidden_fields)),
    ],
)
if field_metadata:
    raise RuntimeError(
        f"{field_metadata} platform migration fields remain in metadata.",
    )
model_metadata = env["ir.model"].sudo().search_count(
    [("model", "in", sorted(forbidden_models))],
)
if model_metadata:
    raise RuntimeError(
        f"{model_metadata} platform migration models remain in metadata.",
    )
xmlids = env["ir.model.data"].sudo().search_count(
    [("module", "=", "usl_platform_billing_restore")],
)
menu_xmlids = env["ir.model.data"].sudo().search_count(
    [
        ("module", "=", "usl_platform_billing_restore"),
        ("model", "=", "ir.ui.menu"),
    ],
)
if menu_xmlids:
    raise RuntimeError(
        f"{menu_xmlids} platform migration menus remain after finalization.",
    )
if xmlids:
    raise RuntimeError(
        f"{xmlids} platform migration XML IDs remain after finalization.",
    )
product_module = env["ir.module.module"].sudo().search(
    [("name", "=", "usl_platform_billing")],
    limit=1,
)
if not product_module or product_module.state != "installed":
    message = "The platform billing product module is not installed."
    raise RuntimeError(message)

summary = {
    "migration_module_state": (
        migration_module.state if migration_module else "absent"
    ),
    "migration_addons_path_active": False,
    "migration_menu_xmlids": menu_xmlids,
    "migration_models_loaded": 0,
    "migration_model_metadata": model_metadata,
    "migration_field_metadata": field_metadata,
    "migration_fields_on_product_models": 0,
    "migration_xmlids": xmlids,
    "product_module_state": product_module.state,
    "platforms": env["usl.platform.billing.platform"]
    .sudo()
    .with_context(active_test=False)
    .search_count([]),
    "sessions": env["usl.platform.billing.session"].sudo().search_count([]),
    "payouts": env["usl.platform.billing.payout"].sudo().search_count([]),
}
print(json.dumps(summary, indent=2, sort_keys=True))
