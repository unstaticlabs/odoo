# ruff: noqa: F821, T201

import json


run = env["usl.hr.restore.run"].sudo().search([], order="id desc", limit=1)
assert run and run.status == "passed"
models = (
    "hr.contract.type", "hr.department", "hr.departure.reason", "hr.employee",
    "hr.job", "hr.payroll.structure.type", "hr.resume.line.type", "hr.skill",
    "hr.skill.level", "hr.skill.type", "hr.version", "hr.work.location",
    "resource.calendar", "resource.calendar.attendance", "resource.resource",
)
before = {
    model: env[model].sudo().with_context(active_test=False).search_count([])
    for model in models
}
module = env["ir.module.module"].sudo().search(
    [("name", "=", "usl_hr_restore")], limit=1,
)
module.button_immediate_uninstall()
env.cr.commit()
after = {
    model: env[model].sudo().with_context(active_test=False).search_count([])
    for model in models
}
assert before == after
print(json.dumps({"migration_module": "uninstalled", "before": before, "after": after}, indent=2, sort_keys=True))
