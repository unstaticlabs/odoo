# ruff: noqa: EM101, F821, T201

import json


def rows(query, parameters=()):
    env.cr.execute(query, parameters)
    return env.cr.fetchall()


module_states = dict(
    rows(
        "SELECT name, state FROM ir_module_module WHERE name = ANY(%s)",
        (["usl_b2c", "usl_b2c_restore"],),
    ),
)
if module_states.get("usl_b2c") != "installed":
    raise RuntimeError("The delivered usl_b2c module is not installed.")
if module_states.get("usl_b2c_restore") not in {
    None,
    "uninstalled",
    "uninstallable",
}:
    raise RuntimeError(
        f"The temporary B2C restoration module remains "
        f"{module_states['usl_b2c_restore']}.",
    )

if "usl.b2c.restore.run" in env.registry.models:
    raise RuntimeError("The temporary B2C restoration model remains loaded.")
if rows(
    "SELECT count(*) FROM ir_model WHERE model = %s",
    ("usl.b2c.restore.run",),
)[0][0]:
    raise RuntimeError("Temporary B2C model metadata remains.")
if rows(
    "SELECT count(*) FROM ir_model_data WHERE module = %s",
    ("usl_b2c_restore",),
)[0][0]:
    raise RuntimeError("Temporary B2C XML IDs remain.")
if rows("SELECT to_regclass(%s)", ("public.usl_b2c_restore_run",))[0][0]:
    raise RuntimeError("The temporary B2C restoration table remains.")

product_models = (
    "b2c.channel",
    "b2c.order",
    "b2c.order.line",
    "b2c.order.source",
    "b2c.payment.event",
    "b2c.fulfilment.event",
    "b2c.product.alias",
    "b2c.accounting.session",
    "b2c.accounting.link",
    "b2c.provider.evidence",
)
missing = sorted(set(product_models) - set(env.registry.models))
if missing:
    raise RuntimeError(f"Delivered B2C product models are missing: {missing}.")

summary = {
    "migration_module_state": module_states.get("usl_b2c_restore", "missing"),
    "migration_model_metadata": 0,
    "migration_models_loaded": 0,
    "migration_table": 0,
    "migration_xmlids": 0,
    "product_module_state": module_states["usl_b2c"],
    "product_records": {
        model: env[model].sudo().search_count([]) for model in product_models
    },
}
print(json.dumps(summary, indent=2, sort_keys=True))
