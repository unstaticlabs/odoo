# ruff: noqa: F821, T201

import json


def fail(message):
    raise RuntimeError(message)


module = env["ir.module.module"].sudo().search(
    [("name", "=", "usl_tese_restore")],
    limit=1,
)
if module and module.state not in {"uninstalled", "uninstallable"}:
    fail(f"TESE migration module remains active: {module.state}.")

forbidden_models = {
    "usl.tese.restore.run",
    "usl.tese.restore.mapping",
    "usl.tese.restore.issue",
}
loaded = forbidden_models & set(env.registry.models)
if loaded:
    fail(f"TESE migration-only models remain loaded: {loaded}.")
metadata = env["ir.model"].sudo().search_count([
    ("model", "in", sorted(forbidden_models)),
])
if metadata:
    fail(
        f"{metadata} TESE migration-only model definitions remain.",
    )
xmlids = env["ir.model.data"].sudo().search_count([
    ("module", "=", "usl_tese_restore"),
])
if xmlids:
    fail(f"{xmlids} TESE migration XML IDs remain.")
product = env["ir.module.module"].sudo().search(
    [("name", "=", "usl_tese_payroll")],
    limit=1,
)
if not product or product.state != "installed":
    fail("The Paie TESE product module is not installed.")

payrolls = env["usl.tese.payslip"].sudo().search([])
product_counts = {
    "profiles": env["usl.tese.profile"].sudo().with_context(
        active_test=False,
    ).search_count([]),
    "payslips": len(payrolls),
    "payroll_moves": len(payrolls.mapped("move_id")),
    "payroll_pdfs": len(payrolls.mapped("attachment_id")),
    "paid": len(payrolls.filtered(lambda item: item.state == "paid")),
    "to_reconcile": len(
        payrolls.filtered(lambda item: item.state == "to_reconcile"),
    ),
}
expected_counts = {
    "profiles": 4,
    "payslips": 9,
    "payroll_moves": 9,
    "payroll_pdfs": 9,
    "paid": 5,
    "to_reconcile": 4,
}
if product_counts != expected_counts:
    fail(
        f"Final Paie TESE business counts differ: {product_counts}.",
    )

summary = {
    "migration_module_state": module.state if module else "absent",
    "migration_models_loaded": 0,
    "migration_model_metadata": metadata,
    "migration_xmlids": xmlids,
    "product_module_state": product.state,
    "employees": env["hr.employee"].sudo().search_count([]),
    **product_counts,
}
print(json.dumps(summary, indent=2, sort_keys=True))
