# ruff: noqa: F821, T201

import json


def fail(message):
    raise RuntimeError(message)


def business_counts():
    return {
        "employees": env["hr.employee"].sudo().search_count([]),
        "versions": env["hr.version"].sudo().search_count([]),
        "profiles": env["usl.tese.profile"].sudo().with_context(
            active_test=False,
        ).search_count([]),
        "payslips": env["usl.tese.payslip"].sudo().search_count([]),
        "payroll_moves": env["account.move"].sudo().search_count([
            ("tese_payslip_id", "!=", False),
            ("tese_move_role", "=", "payroll"),
        ]),
        "payroll_pdfs": len(
            env["usl.tese.payslip"].sudo().search([]).mapped("attachment_id"),
        ),
    }


module = env["ir.module.module"].sudo().search(
    [("name", "=", "usl_tese_restore")],
    limit=1,
)
if not module or module.state != "installed":
    fail(
        "usl_tese_restore must be installed before finalization.",
    )
run = env["usl.tese.restore.run"].sudo().search([], limit=1)
if not run or run.status != "passed":
    fail(
        "The latest TESE restoration must pass before finalization.",
    )
if run.issue_ids.filtered(lambda issue: issue.severity == "error"):
    fail(
        "Blocking TESE restoration issues prevent finalization.",
    )

before = business_counts()
module.button_immediate_uninstall()
env.cr.commit()
after = business_counts()
if after != before:
    fail(
        f"TESE business counts changed during finalization: {before} -> {after}.",
    )
print(json.dumps({
    "migration_module": "uninstalled",
    "business_counts_before": before,
    "business_counts_after": after,
}, indent=2, sort_keys=True))
