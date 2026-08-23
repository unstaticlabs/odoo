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
    "to_post": len(payrolls.filtered(lambda item: item.state == "to_post")),
}
if product_counts["payroll_moves"] != product_counts["payslips"]:
    fail("Every finalized Paie TESE record must retain its accounting entry.")
if any(len(payroll.component_line_ids) != 11 for payroll in payrolls):
    fail("Every finalized Paie TESE record must retain 11 accounting components.")
if any(
    not payroll.currency_id.is_zero(payroll.balance_difference)
    for payroll in payrolls
):
    fail("Every finalized Paie TESE record must remain balanced.")
posted = payrolls.filtered(lambda payroll: payroll.move_id.state == "posted")
draft = payrolls.filtered(lambda payroll: payroll.move_id.state == "draft")
if len(posted) + len(draft) != len(payrolls):
    fail("A finalized Paie TESE entry has an unsupported accounting state.")
if any(not payroll.attachment_id for payroll in posted):
    fail("Every posted Paie TESE record must retain its provider PDF.")
if any(payroll.state not in {"paid", "to_reconcile"} for payroll in posted):
    fail("A posted Paie TESE record has an invalid workflow state.")
if any(payroll.state not in {"prepared", "to_post"} for payroll in draft):
    fail(
        "A draft Paie TESE entry must remain prepared or ready for posting.",
    )
if any(not payroll.profile_id for payroll in payrolls):
    fail("Every finalized Paie TESE record must retain its payroll profile.")

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
