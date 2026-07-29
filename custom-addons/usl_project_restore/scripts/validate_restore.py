# ruff: file-ignore[undefined-name, print, unsorted-imports]

import hashlib
import json


run = env["usl.project.restore.run"].sudo().search([], order="id desc", limit=1)
assert run and run.status == "passed", "Latest Projects restoration did not pass."

statistics = run.statistics_json
assert statistics["source"] == statistics["target"], (
    "Source and target material counts differ."
)

models = {
    "project.project": "project.project",
    "project.task": "project.task",
    "project.project.stage": "project.project.stage",
    "project.task.type": "project.task.type",
    "project.tags": "project.tags",
    "project.milestone": "project.milestone",
    "project.task.recurrence": "project.task.recurrence",
    "project.update": "project.update",
    "mail.message": "mail.message",
    "mail.tracking.value": "mail.tracking.value",
    "mail.activity": "mail.activity",
    "ir.attachment": "ir.attachment",
}
duplicate_traces = {}
for target_model, source_model in models.items():
    env.cr.execute(
        f"""
            SELECT rebuild_source_id, count(*)
             FROM {env[target_model]._table}
             WHERE rebuild_source_model = %s
               AND rebuild_source_snapshot = %s
             GROUP BY rebuild_source_id
            HAVING count(*) > 1
        """,
        (source_model, run.source_snapshot),
    )
    duplicate_traces[target_model] = env.cr.fetchall()
assert not any(duplicate_traces.values()), "Duplicate source identities found."

projects = (
    env["project.project"]
    .sudo()
    .with_context(active_test=False)
    .search(
        [
            ("rebuild_source_model", "=", "project.project"),
            ("rebuild_source_snapshot", "=", run.source_snapshot),
        ],
    )
)
tasks = (
    env["project.task"]
    .sudo()
    .with_context(active_test=False)
    .search(
        [
            ("rebuild_source_model", "=", "project.task"),
            ("rebuild_source_snapshot", "=", run.source_snapshot),
        ],
    )
)
attachments = (
    env["ir.attachment"]
    .sudo()
    .search(
        [
            ("rebuild_source_model", "=", "ir.attachment"),
            ("rebuild_source_snapshot", "=", run.source_snapshot),
        ],
    )
)

bad_attachment_checksums = [
    attachment.rebuild_source_id
    for attachment in attachments
    if hashlib.sha1(
        env["ir.attachment"].sudo().browse(attachment.id).raw,
    ).hexdigest()
    != attachment.checksum
]
assert not bad_attachment_checksums, "Restored attachment bytes are invalid."

privacy_counts = {
    privacy: len(
        projects.filtered(
            lambda project: project.privacy_visibility == privacy,
        ),
    )
    for privacy in ("employees", "followers", "invited_users", "portal")
}
state_counts = {
    state: len(tasks.filtered(lambda task: task.state == state))
    for state in sorted(set(tasks.mapped("state")))
}

valentin = env["res.users"].sudo().search([("login", "=", "valentin")], limit=1)
roger = env["res.users"].sudo().search(
    [("login", "=", "roger@unstaticlabs.com")],
    limit=1,
)
prosper = env["res.users"].sudo().search([("login", "=", "prosper")], limit=1)
assert valentin and roger and prosper, "Representative target users are missing."
prosper_projects = (
    env["project.project"]
    .with_user(prosper)
    .with_context(active_test=False)
    .search([])
)
private_projects = projects.filtered(
    lambda project: project.privacy_visibility == "followers",
)
assert not (prosper_projects & private_projects), (
    "Accounting-only reviewer can see follower-only Projects."
)

summary = {
    "run_id": run.id,
    "status": run.status,
    "material_counts": statistics["target"],
    "active_projects": len(projects.filtered("active")),
    "archived_projects": len(projects - projects.filtered("active")),
    "active_tasks": len(tasks.filtered("active")),
    "archived_tasks": len(tasks - tasks.filtered("active")),
    "task_templates": len(tasks.filtered("is_template")),
    "planned_ranges": len(tasks.filtered("planned_date_begin")),
    "subtasks": len(tasks.filtered("parent_id")),
    "dependency_links": sum(len(task.depend_on_ids) for task in tasks),
    "state_counts": state_counts,
    "privacy_counts": privacy_counts,
    "analytic_projects": len(projects.filtered("account_id")),
    "attachment_bytes": sum(attachments.mapped("file_size")),
    "restricted_reviewer_visible_projects": len(prosper_projects),
    "restricted_reviewer_private_projects": len(
        prosper_projects & private_projects,
    ),
    "roger_visible_projects": (
        env["project.project"]
        .with_user(roger)
        .with_context(active_test=False)
        .search_count([])
    ),
    "valentin_visible_projects": (
        env["project.project"]
        .with_user(valentin)
        .with_context(active_test=False)
        .search_count([])
    ),
    "duplicate_source_identities": duplicate_traces,
}
assert summary["dependency_links"] == statistics["target"]["dependencies"]
assert summary["active_projects"] + summary["archived_projects"] == len(projects)
assert summary["active_tasks"] + summary["archived_tasks"] == len(tasks)

print(json.dumps(summary, indent=2, sort_keys=True))
