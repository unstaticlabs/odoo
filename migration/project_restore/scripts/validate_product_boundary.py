# ruff: noqa: EM101, F821, I001, T201

import json


migration_modules = env["ir.module.module"].sudo().search(
    [
        (
            "name",
            "in",
            [
                "usl_identity_restore",
                "usl_b2c_restore",
                "usl_hr_restore",
                "usl_product_restore",
                "usl_project_restore",
            ],
        ),
    ],
)
active_migration_modules = migration_modules.filtered(
    lambda module: module.state not in {"uninstalled", "uninstallable"},
)
if active_migration_modules:
    raise RuntimeError(
        "Migration modules remain active: "
        f"{sorted((module.name, module.state) for module in active_migration_modules)}.",
    )

forbidden_models = {
    "usl.b2c.restore.run",
    "usl.identity.restore.run",
    "usl.hr.restore.run",
    "usl.product.restore.run",
    "usl.project.restore.run",
    "usl.project.restore.issue",
}
loaded_forbidden_models = forbidden_models & set(env.registry.models)
if loaded_forbidden_models:
    raise RuntimeError(
        f"Migration-only models remain loaded: {sorted(loaded_forbidden_models)}.",
    )

business_models = {
    "hr.employee.type",
    "hr.department",
    "hr.departure.reason",
    "hr.job",
    "hr.payroll.structure.type",
    "hr.resume.line.type",
    "hr.skill",
    "hr.skill.level",
    "hr.skill.type",
    "hr.version",
    "hr.work.location",
    "project.project",
    "project.task",
    "project.project.stage",
    "project.task.type",
    "project.tags",
    "project.milestone",
    "project.task.recurrence",
    "project.update",
    "mail.message",
    "mail.activity",
    "mail.activity.type",
    "mail.tracking.value",
    "mail.alias",
    "mail.followers",
    "res.partner.bank",
    "res.partner.category",
    "res.partner.industry",
    "res.users",
    "resource.calendar",
    "resource.calendar.attendance",
    "resource.resource",
    "product.attribute",
    "product.category",
    "product.pricelist",
    "product.template",
}
forbidden_fields = {
    "rebuild_source_database",
    "rebuild_source_model",
    "rebuild_source_id",
    "rebuild_source_snapshot",
    "rebuild_import_status",
    "rebuild_import_note",
    "usl_source_task_properties",
    "usl_source_task_properties_definition",
}
remaining_fields = sorted(
    (model_name, field_name)
    for model_name in business_models
    for field_name in forbidden_fields
    if field_name in env[model_name]._fields
)
if remaining_fields:
    raise RuntimeError(
        f"Migration-only fields remain on product models: {remaining_fields}.",
    )

remaining_field_metadata = env["ir.model.fields"].sudo().search_count(
    [
        ("model", "in", sorted(business_models)),
        ("name", "in", sorted(forbidden_fields)),
    ],
)
if remaining_field_metadata:
    raise RuntimeError(
        f"{remaining_field_metadata} migration-only field definitions remain "
        "in product model metadata.",
    )

technical_xmlids = env["ir.model.data"].sudo().search_count(
    [
        (
            "module",
            "in",
            [
                "usl_identity_restore",
                "usl_hr_restore",
                "usl_product_restore",
                "usl_project_restore",
            ],
        ),
    ],
)
if technical_xmlids:
    raise RuntimeError(
        f"{technical_xmlids} migration XML IDs remain after finalization.",
    )

migration_model_metadata = env["ir.model"].sudo().search_count(
    [("model", "in", sorted(forbidden_models))],
)
if migration_model_metadata:
    raise RuntimeError(
        f"{migration_model_metadata} migration-only model definitions remain.",
    )

technical_views = env["ir.ui.view"].sudo().search_count(
    [
        ("model", "in", ["project.project", "project.task"]),
        "|",
        "|",
        ("name", "ilike", "restor"),
        ("arch_db", "ilike", "source snapshot"),
        ("arch_db", "ilike", "source task properties"),
    ],
)
if technical_views:
    raise RuntimeError(
        f"{technical_views} project views still expose migration terminology.",
    )

product_module = env["ir.module.module"].sudo().search(
    [("name", "=", "usl_project")],
    limit=1,
)
if not product_module or product_module.state != "installed":
    raise RuntimeError("The clean usl_project product module is not installed.")
if "planned_date_begin" not in env["project.task"]._fields:
    raise RuntimeError("The operational Planned Start field is unavailable.")

summary = {
    "migration_module_states": {
        module.name: module.state for module in migration_modules
    },
    "migration_model_metadata": migration_model_metadata,
    "migration_models_loaded": 0,
    "migration_field_metadata": remaining_field_metadata,
    "migration_fields_on_product_models": 0,
    "migration_project_views": technical_views,
    "migration_xmlids": technical_xmlids,
    "product_module_state": product_module.state,
    "projects": env["project.project"]
    .with_context(active_test=False)
    .sudo()
    .search_count([]),
    "tasks": env["project.task"]
    .with_context(active_test=False)
    .sudo()
    .search_count([]),
}
print(json.dumps(summary, indent=2, sort_keys=True))
