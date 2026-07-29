# ruff: file-ignore[raw-string-in-exception, undefined-name, print]

import json


def business_counts():
    return {
        "projects": env["project.project"]
        .with_context(active_test=False)
        .sudo()
        .search_count([]),
        "tasks": env["project.task"]
        .with_context(active_test=False)
        .sudo()
        .search_count([]),
        "messages": env["mail.message"].sudo().search_count(
            [("model", "in", ["project.project", "project.task"])],
        ),
        "attachments": env["ir.attachment"].sudo().search_count(
            [("res_model", "in", ["project.project", "project.task"])],
        ),
    }


module = env["ir.module.module"].sudo().search(
    [("name", "=", "usl_project_restore")],
    limit=1,
)
if not module or module.state != "installed":
    raise RuntimeError(
        "usl_project_restore must be installed and validated before finalization.",
    )

latest_run = env["usl.project.restore.run"].sudo().search([], limit=1)
blocking_issues = (
    latest_run.issue_ids.filtered(
        lambda issue: (
            issue.severity in {"warning", "error"} and not issue.resolved
        ),
    )
    if latest_run
    else False
)
if not latest_run or latest_run.status != "passed" or blocking_issues:
    raise RuntimeError(
        "The latest project restoration must pass without unresolved "
        "warnings or errors before finalization.",
    )

before = business_counts()
module.button_immediate_uninstall()
env.cr.commit()
after = business_counts()
if after != before:
    raise RuntimeError(
        f"Project business counts changed during finalization: {before} -> {after}.",
    )

print(
    json.dumps(
        {
            "migration_module": "uninstalled",
            "business_counts_before": before,
            "business_counts_after": after,
        },
        indent=2,
        sort_keys=True,
    ),
)
