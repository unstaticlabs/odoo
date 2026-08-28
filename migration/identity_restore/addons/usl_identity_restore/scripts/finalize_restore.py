# ruff: noqa: F821, I001, T201

import json


run = env["usl.identity.restore.run"].sudo().search([], order="id desc", limit=1)
assert run and run.status == "passed", "Identity restoration must pass before finalization."
preference_dispositions = run.statistics_json.get("preference_dispositions", {})
assert preference_dispositions.get("status") != "deferred", (
    "Saved preferences must be finalized after Projects before Identity cleanup."
)
assert len(preference_dispositions.get("filters", {}).get("target_ids", [])) == 7, (
    "All seven approved saved filters must exist before Identity cleanup."
)
assert preference_dispositions.get("home", {}).get("favorite_count", 0) >= 7, (
    "Valentin's source-backed Home must be built before Identity cleanup."
)
valentin = env["res.users"].sudo().search([
    ("login", "=", preference_dispositions["home"]["user_login"]),
], limit=1)
assert valentin and valentin.action_id.id == env.ref("usl_home.action_usl_home").id, (
    "Valentin's Home action must be active before Identity cleanup."
)
before = {
    "companies": env["res.company"].sudo().with_context(active_test=False).search_count([]),
    "partners": env["res.partner"].sudo().with_context(active_test=False).search_count([]),
    "users": env["res.users"].sudo().with_context(active_test=False).search_count([]),
    "banks": env["res.partner.bank"].sudo().with_context(active_test=False).search_count([]),
}
module = env["ir.module.module"].sudo().search([("name", "=", "usl_identity_restore")], limit=1)
module.button_immediate_uninstall()
env.cr.commit()
after = {
    "companies": env["res.company"].sudo().with_context(active_test=False).search_count([]),
    "partners": env["res.partner"].sudo().with_context(active_test=False).search_count([]),
    "users": env["res.users"].sudo().with_context(active_test=False).search_count([]),
    "banks": env["res.partner.bank"].sudo().with_context(active_test=False).search_count([]),
}
assert before == after, f"Identity business rows changed on finalization: {before} != {after}"
print(json.dumps({"migration_module": "uninstalled", "before": before, "after": after}, indent=2, sort_keys=True))
