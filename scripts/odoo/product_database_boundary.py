import os

# Odoo shell acceptance script: terminal output is part of its contract.
# ruff: noqa: T201

MIGRATION_MODULES = {
    "usl_accounting_restore",
    "usl_b2c_restore",
    "usl_hr_restore",
    "usl_identity_restore",
    "usl_platform_billing_restore",
    "usl_product_restore",
    "usl_project_restore",
    "usl_tese_restore",
    "usl_collaboration_restore",
}
PRODUCT_MODULES = {
    "rebuild_account_migration",
    "usl_access_control",
    "usl_accounting",
    "usl_b2c",
    "usl_documents",
    "usl_documents_accounting",
    "usl_documents_b2c",
    "usl_expense_batch",
    "usl_feedback",
    "usl_home",
    "usl_locale",
    "usl_platform_billing",
    "usl_platform_billing_pocketid",
    "usl_pocketid",
    "usl_project",
    "usl_sign",
    "usl_tese_accounting",
    "usl_tese_payroll",
}
EXCLUDED_AUTO_INSTALL_MODULES = {
    "gamification",
    "hr_gamification",
    "hr_skills_survey",
    "html_builder",
    "link_tracker",
    "mail_plugin",
    "mass_mailing",
    "mass_mailing_sale",
    "mass_mailing_themes",
    "project_mail_plugin",
    "social_media",
    "survey",
}
FORBIDDEN_MODELS = {
    "rebuild.account.analytic.override",
    "rebuild.account.deferred.schedule.line",
    "rebuild.account.discrepancy",
    "rebuild.account.import.run",
    "rebuild.account.source.report",
}
FORBIDDEN_MODEL_PREFIXES = (
    "usl.b2c.restore",
    "usl.collaboration.restore",
    "usl.hr.restore",
    "usl.identity.restore",
    "usl.platform.billing.restore",
    "usl.product.restore",
    "usl.project.restore",
    "usl.tese.restore",
)
FORBIDDEN_TABLES = {
    model.replace(".", "_") for model in FORBIDDEN_MODELS
}
FORBIDDEN_TABLE_PREFIXES = tuple(
    prefix.replace(".", "_") for prefix in FORBIDDEN_MODEL_PREFIXES
)


def rows(query, parameters=()):
    env.cr.execute(query, parameters)  # noqa: F821
    return env.cr.fetchall()  # noqa: F821


def table_exists(table):
    return bool(rows("SELECT to_regclass(%s)", (f"public.{table}",))[0][0])


errors = []
if env.cr.dbname != os.environ.get("ODOO_INIT_DB", "odoo_dev"):  # noqa: F821
    errors.append("The boundary check is connected to an unexpected database.")
if env.cr.dbname == "odoo_online_source_saas_19_3":  # noqa: F821
    errors.append("The read-only Online source database is not a product target.")
if os.environ.get("USL_EINVOICE_LIVE_ENABLED", "0") != "0":
    errors.append("Electronic-invoice live access is enabled.")
if os.environ.get("USL_EREPORTING_LIVE_ENABLED", "0") != "0":
    errors.append("E-reporting live access is enabled.")

qa_profile_rows = rows(
    "SELECT value FROM ir_config_parameter WHERE key = %s",
    ("usl.qa.data_profile",),
)
qa_profile = qa_profile_rows[0][0] if qa_profile_rows else "full"
if os.environ.get("USL_PRODUCT_BOUNDARY_PREPROD", "0") == "1" and qa_profile != "full":
    errors.append(
        f"Pre-production cannot use partial QA data profile {qa_profile}.",
    )

module_states = dict(rows(
    "SELECT name, state FROM ir_module_module WHERE name = ANY(%s)",
    (
        list(
            MIGRATION_MODULES
            | PRODUCT_MODULES
            | EXCLUDED_AUTO_INSTALL_MODULES
            | {"usl_bootstrap"},
        ),
    ),
))
for module_name in sorted(MIGRATION_MODULES):
    if module_states.get(module_name) not in {None, "uninstalled", "uninstallable"}:
        errors.append(
            f"Migration module {module_name} remains {module_states[module_name]}.",
        )
for module_name in sorted(PRODUCT_MODULES):
    if module_states.get(module_name) != "installed":
        errors.append(
            f"Product module {module_name} is not installed "
            f"({module_states.get(module_name, 'missing')}).",
        )
if module_states.get("usl_bootstrap") == "installed":
    errors.append("Test-only usl_bootstrap is installed in the product target.")
for module_name in sorted(EXCLUDED_AUTO_INSTALL_MODULES):
    if module_states.get(module_name) not in {None, "uninstalled", "uninstallable"}:
        errors.append(
            f"Excluded optional product module {module_name} remains "
            f"{module_states[module_name]}.",
        )

if table_exists("ir_module_module_dependency"):
    dependency_rows = rows(
        """
        SELECT module.name, dependency.name
          FROM ir_module_module_dependency dependency
          JOIN ir_module_module module ON module.id = dependency.module_id
         WHERE module.state = 'installed'
           AND dependency.name = ANY(%s)
        """,
        (list(MIGRATION_MODULES),),
    )
    for module_name, dependency_name in dependency_rows:
        errors.append(
            f"Installed module {module_name} depends on migration module "
            f"{dependency_name}.",
        )

for model_name, in rows("SELECT model FROM ir_model"):
    if model_name in FORBIDDEN_MODELS or model_name.startswith(
        FORBIDDEN_MODEL_PREFIXES,
    ):
        errors.append(f"Migration model remains registered: {model_name}.")

field_rows = rows(
    """
    SELECT model, name
      FROM ir_model_fields
     WHERE name LIKE %s
        OR name IN (
            'source_snapshot', 'source_dump_sha256',
            'x_content_bank_candidate'
        )
    """,
    ("rebuild_source_%",),
)
for model_name, field_name in field_rows:
    errors.append(f"Migration field remains registered: {model_name}.{field_name}.")

xml_rows = rows(
    "SELECT module, name FROM ir_model_data WHERE module = ANY(%s)",
    (list(MIGRATION_MODULES),),
)
for module_name, xml_name in xml_rows:
    errors.append(f"Migration XML ID remains: {module_name}.{xml_name}.")

if table_exists("ir_act_window"):
    for action_name, res_model in rows(
        "SELECT name::text, res_model FROM ir_act_window WHERE res_model IS NOT NULL",
    ):
        if res_model in FORBIDDEN_MODELS or res_model.startswith(
            FORBIDDEN_MODEL_PREFIXES,
        ):
            errors.append(
                f"Migration window action remains: {action_name} ({res_model}).",
            )

schema_tables = {
    name for name, in rows(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'",
    )
}
for table_name in sorted(schema_tables):
    if table_name in FORBIDDEN_TABLES or table_name.startswith(
        FORBIDDEN_TABLE_PREFIXES,
    ):
        errors.append(f"Migration table remains in the schema: {table_name}.")

schema_columns = rows(
    """
    SELECT table_name, column_name
      FROM information_schema.columns
     WHERE table_schema = 'public'
       AND (
            column_name LIKE %s
         OR column_name IN (
            'source_snapshot', 'source_dump_sha256',
            'x_content_bank_candidate'
         )
       )
    """,
    ("rebuild_source_%",),
)
for table_name, column_name in schema_columns:
    errors.append(
        f"Migration column remains in the schema: {table_name}.{column_name}.",
    )

if errors:
    raise RuntimeError(
        "Product database boundary failed:\n- " + "\n- ".join(sorted(errors)),
    )

print(
    "Product database boundary: PASS "
    f"({len(PRODUCT_MODULES)} product modules, no migration registry/schema residue)",
)
