# ruff: noqa: F821, I001, T201

import hashlib
import json
import os
from collections import defaultdict
from decimal import Decimal

from odoo.addons.usl_project_restore.models.restore import ProjectSourceReader
from odoo.tools import html2plaintext, html_sanitize


source_options = {
    "host": os.getenv("PROJECT_SOURCE_DB_HOST", "accounting-source-db"),
    "port": int(os.getenv("PROJECT_SOURCE_DB_PORT", "5432")),
    "user": os.getenv("PROJECT_SOURCE_DB_USER", "odoo"),
    "password": os.getenv("PROJECT_SOURCE_DB_PASSWORD", "odoo"),
    "database": os.getenv(
        "PROJECT_SOURCE_DATABASE",
        "odoo_online_source_saas_19_3",
    ),
    "snapshot": os.getenv(
        "PROJECT_SOURCE_SNAPSHOT",
        "odoo-online-saas-19.3-projects",
    ),
}
source = ProjectSourceReader(source_options).read()


run = env["usl.project.restore.run"].sudo().search([], order="id desc", limit=1)
assert run and run.status == "passed", "Latest Projects restoration did not pass."

statistics = run.statistics_json
assert statistics["source"] == statistics["target"], (
    "Source and target material counts differ."
)
assert source["counts"] == statistics["source"], (
    "Current source inventory differs from the restoration-run inventory."
)

models = {
    "res.company": "res.company",
    "res.partner": "res.partner",
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
    "mail.alias": "mail.alias",
    "mail.followers": "mail.followers",
    "ir.attachment": "ir.attachment",
}
duplicate_traces = {}
for target_model, source_model in models.items():
    env.cr.execute(
        f"""
            SELECT rebuild_source_id, count(*)
             FROM {env[target_model]._table}
             WHERE rebuild_source_model = %s
               AND rebuild_source_database = %s
             GROUP BY rebuild_source_id
            HAVING count(*) > 1
        """,
        (source_model, run.source_database),
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
            (
                "rebuild_source_id",
                "in",
                [row["id"] for row in source["attachments"]] or [0],
            ),
        ],
    )
)


def source_text(value):
    if isinstance(value, dict):
        return (
            value.get("en_US")
            or value.get("fr_FR")
            or next(iter(value.values()), "")
        )
    return value or ""


def normalized(value):
    if value is False or value is None:
        return None
    if isinstance(value, memoryview):
        return value.tobytes().hex()
    if isinstance(value, (Decimal, int, float)) and not isinstance(value, bool):
        return format(Decimal(str(value)).normalize(), "f")
    if isinstance(value, dict):
        return {
            str(key): normalized(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple, set)):
        return [normalized(item) for item in value]
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat(sep=" ")
        except TypeError:
            return value.isoformat()
    if isinstance(value, (bool, str)):
        return value
    return str(value)


def canonical_digest(rows):
    canonical = {
        str(source_id): normalized(values)
        for source_id, values in rows
    }
    return hashlib.sha256(
        json.dumps(
            canonical,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode(),
    ).hexdigest()


def normalized_message_body(value):
    """Compare the rendered text preserved by the native 19.3 sanitizer."""
    return html2plaintext(html_sanitize(str(value or ""))).strip()


def source_id(record):
    return record.rebuild_source_id if record else None


def external_id(record):
    return record.get_external_id().get(record.id) if record else None


def traced_map(
    model,
    source_model,
    *,
    snapshot=run.source_snapshot,
    source_ids=None,
):
    domain = [
        ("rebuild_source_database", "=", run.source_database),
        ("rebuild_source_model", "=", source_model),
    ]
    if snapshot:
        domain.append(("rebuild_source_snapshot", "=", snapshot))
    if source_ids is not None:
        domain.append(("rebuild_source_id", "in", list(source_ids) or [0]))
    return {
        record.rebuild_source_id: record
        for record in (
            env[model]
            .sudo()
            .with_context(active_test=False)
            .search(domain)
        )
    }


project_map = {record.rebuild_source_id: record for record in projects}
task_map = {record.rebuild_source_id: record for record in tasks}
attachment_map = traced_map(
    "ir.attachment",
    "ir.attachment",
    source_ids={row["id"] for row in source["attachments"]},
)
project_stage_map = traced_map(
    "project.project.stage",
    "project.project.stage",
)
task_stage_map = traced_map("project.task.type", "project.task.type")
historical_task_stage_map = traced_map(
    "project.task.type",
    "project.task.type.history",
)
tag_map = traced_map("project.tags", "project.tags")
milestone_map = traced_map("project.milestone", "project.milestone")
recurrence_map = traced_map(
    "project.task.recurrence",
    "project.task.recurrence",
)
update_map = traced_map("project.update", "project.update")
message_map = traced_map("mail.message", "mail.message")
tracking_map = traced_map("mail.tracking.value", "mail.tracking.value")
activity_map = traced_map("mail.activity", "mail.activity")
follower_map = traced_map("mail.followers", "mail.followers")
alias_map = traced_map("mail.alias", "mail.alias")
partner_map = traced_map("res.partner", "res.partner", snapshot=None)
company_map = traced_map("res.company", "res.company", snapshot=None)
analytic_map = traced_map(
    "account.analytic.account",
    "account.analytic.account",
    snapshot=None,
)
source_user_login = {
    row["id"]: row["login"]
    for row in source["users"]
}
target_user_login = {}
for row in source["users"]:
    target_partner = partner_map[row["partner_id"]]
    target_user = (
        env["res.users"]
        .sudo()
        .with_context(active_test=False)
        .search([("partner_id", "=", target_partner.id)], limit=1)
    )
    assert target_user, f"Source user {row['id']} has no target user."
    target_user_login[row["id"]] = target_user.login
target_user_logins = set(target_user_login.values())

expected_project_stages = defaultdict(set)
for row in source["project_task_stage_rel"]:
    expected_project_stages[row["project_id"]].add(row["type_id"])
expected_project_tags = defaultdict(set)
for row in source["project_tag_rel"]:
    expected_project_tags[row["project_id"]].add(row["tag_id"])
expected_project_favorites = defaultdict(set)
for row in source["project_favorite_rel"]:
    expected_project_favorites[row["project_id"]].add(
        target_user_login[row["user_id"]],
    )
expected_task_users = defaultdict(set)
for row in source["task_user_rel"]:
    expected_task_users[row["task_id"]].add(
        target_user_login[row["user_id"]],
    )
expected_task_tags = defaultdict(set)
for row in source["task_tag_rel"]:
    expected_task_tags[row["task_id"]].add(row["tag_id"])
expected_dependencies = defaultdict(set)
for row in source["dependencies"]:
    expected_dependencies[row["task_id"]].add(row["depends_on_id"])
expected_message_recipients = defaultdict(set)
for row in source["message_partner_rel"]:
    expected_message_recipients[row["message_id"]].add(row["partner_id"])
expected_message_attachments = defaultdict(set)
for row in source["message_attachment_rel"]:
    expected_message_attachments[row["message_id"]].add(
        row["attachment_id"],
    )
expected_follower_subtypes = defaultdict(set)
for row in source["follower_subtype_rel"]:
    expected_follower_subtypes[row["follower_id"]].add(row["subtype_id"])

assert len(project_map) == len(source["projects"])
assert len(task_map) == len(source["tasks"])
assert all(
    project_map[row["id"]].id == row["id"] for row in source["projects"]
), "Project IDs differ from their Online IDs."
assert all(
    task_map[row["id"]].id == row["id"] for row in source["tasks"]
), "Task IDs differ from their Online IDs."
assert all(
    task_stage_map[row["id"]].id == row["id"]
    for row in source["task_stages"]
), "Task-stage IDs differ from their Online IDs."
assert set(historical_task_stage_map) == set(
    source["historical_task_stage_ids"],
), "Deleted historical task-stage identities are not reserved exactly."
assert all(
    stage.id == source_id and not stage.active and not stage.project_ids
    for source_id, stage in historical_task_stage_map.items()
), "A deleted historical task stage is not a hidden, exact-ID reservation."
assert not env["project.task"].with_context(active_test=False).sudo().search_count(
    [("stage_id", "in", [stage.id for stage in historical_task_stage_map.values()])]
), "A current task points to a deleted historical task stage."
for table, rows in (
    ("project_project", source["projects"]),
    ("project_task", source["tasks"]),
    (
        "project_task_type",
        source["task_stages"]
        + [{"id": stage_id} for stage_id in source["historical_task_stage_ids"]],
    ),
):
    env.cr.execute(f"SELECT last_value, is_called FROM {table}_id_seq")
    last_value, is_called = env.cr.fetchone()
    next_value = last_value + 1 if is_called else last_value
    assert next_value > max(row["id"] for row in rows), (
        f"{table} sequence would reuse an Online ID."
    )
assert env["project.task"].with_context(active_test=False).sudo().search_count([]) == len(
    source["tasks"],
), "Target-only Project tasks remain after reconstruction."
assert len(message_map) == len(source["messages"])
assert len(tracking_map) == len(source["tracking_values"])
assert len(activity_map) == len(source["activities"])
assert len(follower_map) == len(source["followers"])
assert len(attachment_map) == len(source["attachments"])
assert len(alias_map) == source["counts"]["project_aliases"]

source_project_rows = []
target_project_rows = []
for row in source["projects"]:
    project = project_map[row["id"]]
    source_project_rows.append(
        (
            row["id"],
            {
                "name": source_text(row["name"]),
                "label_tasks": source_text(row["label_tasks"]),
                "sequence": row["sequence"],
                "partner": row["partner_id"],
                "company": row["company_id"],
                "manager": target_user_login.get(row["user_id"]),
                "stage": row["stage_id"],
                "analytic": row["account_id"],
                "last_update": row["last_update_id"],
                "color": row["color"] or 0,
                "access_token": row["access_token"],
                "privacy": row["privacy_visibility"],
                "status": row["last_update_status"],
                "date_start": row["date_start"],
                "date": row["date"],
                "properties": row["task_properties_definition"] or [],
                "description": row["description"],
                "active": row["active"],
                "dependencies": row["allow_task_dependencies"],
                "milestones": row["allow_milestones"],
                "recurrences": row["allow_recurring_tasks"],
                "template": row["is_template"],
                "last_stage_update": row["date_last_stage_update"],
                "task_stages": sorted(expected_project_stages[row["id"]]),
                "tags": sorted(expected_project_tags[row["id"]]),
                "favorites": sorted(expected_project_favorites[row["id"]]),
                "alias_id": row["alias_id"],
                "alias_name": row["alias_name"],
                "alias_contact": row["alias_contact"],
            },
        ),
    )
    target_project_rows.append(
        (
            row["id"],
            {
                "name": project.name,
                "label_tasks": project.label_tasks,
                "sequence": project.sequence,
                "partner": source_id(project.partner_id),
                "company": source_id(project.company_id),
                "manager": project.user_id.login,
                "stage": source_id(project.stage_id),
                "analytic": source_id(project.account_id),
                "last_update": source_id(project.last_update_id),
                "color": project.color,
                "access_token": project.access_token,
                "privacy": project.privacy_visibility,
                "status": project.last_update_status,
                "date_start": project.date_start,
                "date": project.date,
                "properties": (
                    project.usl_source_task_properties_definition or []
                ),
                "description": project.description,
                "active": project.active,
                "dependencies": project.allow_task_dependencies,
                "milestones": project.allow_milestones,
                "recurrences": project.allow_recurring_tasks,
                "template": project.is_template,
                "last_stage_update": project.date_last_stage_update,
                "task_stages": sorted(
                    source_id(stage)
                    for stage in project.type_ids
                    if source_id(stage)
                ),
                "tags": sorted(
                    source_id(tag)
                    for tag in project.tag_ids
                    if source_id(tag)
                ),
                "favorites": sorted(
                    user.login
                    for user in project.favorite_user_ids
                    if user.login in target_user_logins
                ),
                "alias_id": source_id(project.alias_id),
                "alias_name": project.alias_id.alias_name,
                "alias_contact": project.alias_id.alias_contact,
            },
        ),
    )

source_task_rows = []
target_task_rows = []
for row in source["tasks"]:
    task = task_map[row["id"]]
    source_task_rows.append(
        (
            row["id"],
            {
                "name": row["name"],
                "sequence": row["sequence"],
                "stage": row["stage_id"],
                "project": row["project_id"],
                "partner": row["partner_id"],
                "company": row["company_id"],
                "parent": row["parent_id"],
                "milestone": row["milestone_id"],
                "recurrence": row["recurrence_id"],
                "access_token": row["access_token"],
                "color": row["color"] or 0,
                "priority": row["priority"] or "0",
                "state": row["state"],
                "email_from": row["email_from"],
                "html_history": row["html_field_history"] or {},
                "duration_tracking": row["duration_tracking"],
                "properties": row["task_properties"] or {},
                "description": row["description"],
                "active": row["active"],
                "recurring": row["recurring_task"],
                "template": row["is_template"],
                "date_end": row["date_end"],
                "date_assign": row["date_assign"],
                "deadline": row["date_deadline"],
                "last_stage_update": row["date_last_stage_update"],
                "allocated_hours": row["allocated_hours"] or 0.0,
                "planned_start": row["planned_date_begin"],
                "users": sorted(expected_task_users[row["id"]]),
                "tags": sorted(expected_task_tags[row["id"]]),
                "dependencies": sorted(expected_dependencies[row["id"]]),
            },
        ),
    )
    target_task_rows.append(
        (
            row["id"],
            {
                "name": task.name,
                "sequence": task.sequence,
                "stage": source_id(task.stage_id),
                "project": source_id(task.project_id),
                "partner": source_id(task.partner_id),
                "company": source_id(task.company_id),
                "parent": source_id(task.parent_id),
                "milestone": source_id(task.milestone_id),
                "recurrence": source_id(task.recurrence_id),
                "access_token": task.access_token,
                "color": task.color,
                "priority": task.priority,
                "state": task.state,
                "email_from": task.email_from,
                "html_history": task.html_field_history or {},
                "duration_tracking": task.duration_tracking,
                "properties": task.usl_source_task_properties or {},
                "description": task.description,
                "active": task.active,
                "recurring": task.recurring_task,
                "template": task.is_template,
                "date_end": task.date_end,
                "date_assign": task.date_assign,
                "deadline": task.date_deadline,
                "last_stage_update": task.date_last_stage_update,
                "allocated_hours": task.allocated_hours,
                "planned_start": task.planned_date_begin,
                "users": sorted(
                    user.login
                    for user in task.user_ids
                    if user.login in target_user_logins
                ),
                "tags": sorted(
                    source_id(tag)
                    for tag in task.tag_ids
                    if source_id(tag)
                ),
                "dependencies": sorted(
                    source_id(dependency)
                    for dependency in task.depend_on_ids
                    if source_id(dependency)
                ),
            },
        ),
    )

source_duration_bucket_count = sum(
    len(set((row["duration_tracking"] or {})) - {"d", "s"})
    for row in source["tasks"]
)
target_duration_bucket_count = sum(
    len(set((task_map[row["id"]].duration_tracking or {})) - {"d", "s"})
    for row in source["tasks"]
)
source_duration_total_minutes = sum(
    sum(
        value
        for key, value in (row["duration_tracking"] or {}).items()
        if key not in {"d", "s"}
    )
    for row in source["tasks"]
)
target_duration_total_minutes = sum(
    sum(
        value
        for key, value in (task_map[row["id"]].duration_tracking or {}).items()
        if key not in {"d", "s"}
    )
    for row in source["tasks"]
)
assert target_duration_bucket_count == source_duration_bucket_count, (
    "Task duration bucket count differs from Online."
)
assert target_duration_total_minutes == source_duration_total_minutes, (
    "Task accumulated stage minutes differ from Online."
)
for row in source["tasks"]:
    source_duration = row["duration_tracking"] or {}
    target_duration = task_map[row["id"]].duration_tracking or {}
    assert normalized(target_duration) == normalized(source_duration), (
        f"Task {row['id']} duration ledger differs from Online."
    )
    if source_duration:
        assert target_duration.get("s") == row["stage_id"], (
            f"Task {row['id']} duration ledger has the wrong current stage."
        )
        assert target_duration.get("d") == source_duration.get("d"), (
            f"Task {row['id']} duration clock start differs from Online."
        )

source_message_rows = []
target_message_rows = []
for row in source["messages"]:
    message = message_map[row["id"]]
    source_message_rows.append(
        (
            row["id"],
            {
                "model": row["model"],
                "record": row["res_id"],
                "parent": row["parent_id"],
                "company": row["record_company_id"],
                "subtype": source["xmlids"].get(
                    ("mail.message.subtype", row["subtype_id"]),
                ),
                "activity_type": source["xmlids"].get(
                    ("mail.activity.type", row["mail_activity_type_id"]),
                ),
                "author": row["author_id"],
                "subject": row["subject"],
                "type": row["message_type"],
                "email_from": row["email_from"],
                "message_id": row["message_id"],
                "reply_to": row["reply_to"],
                "body": normalized_message_body(row["body"]),
                "internal": row["is_internal"],
                "date": row["date"],
                "recipients": sorted(
                    expected_message_recipients[row["id"]],
                ),
                "attachments": sorted(
                    expected_message_attachments[row["id"]],
                ),
            },
        ),
    )
    target_message_rows.append(
        (
            row["id"],
            {
                "model": message.model,
                "record": source_id(
                    env[message.model].sudo().browse(message.res_id),
                ),
                "parent": source_id(message.parent_id),
                "company": source_id(message.record_company_id),
                "subtype": external_id(message.subtype_id),
                "activity_type": external_id(message.mail_activity_type_id),
                "author": source_id(message.author_id),
                "subject": message.subject,
                "type": message.message_type,
                "email_from": message.email_from,
                "message_id": message.message_id,
                "reply_to": message.reply_to,
                "body": normalized_message_body(message.body),
                "internal": message.is_internal,
                "date": message.date,
                "recipients": sorted(
                    source_id(partner)
                    for partner in message.partner_ids
                    if source_id(partner)
                ),
                "attachments": sorted(
                    source_id(attachment)
                    for attachment in message.attachment_ids
                    if source_id(attachment)
                ),
            },
        ),
    )

tracking_value_fields = (
    "old_value_integer",
    "new_value_integer",
    "old_value_char",
    "new_value_char",
    "old_value_text",
    "new_value_text",
    "old_value_datetime",
    "new_value_datetime",
    "old_value_float",
    "new_value_float",
    "field_info",
)
source_tracking_rows = []
target_tracking_rows = []
for row in source["tracking_values"]:
    tracking = tracking_map[row["id"]]
    source_tracking_rows.append(
        (
            row["id"],
            {
                "message": row["mail_message_id"],
                "field_model": row["field_model"],
                "field_name": row["field_name"],
                **{
                    field_name: (
                        row[field_name] or 0
                        if field_name
                        in {
                            "old_value_integer",
                            "new_value_integer",
                            "old_value_float",
                            "new_value_float",
                        }
                        else row[field_name]
                    )
                    for field_name in tracking_value_fields
                },
            },
        ),
    )
    target_tracking_rows.append(
        (
            row["id"],
            {
                "message": source_id(tracking.mail_message_id),
                "field_model": tracking.field_id.model,
                "field_name": tracking.field_id.name,
                **{
                    field_name: tracking[field_name]
                    for field_name in tracking_value_fields
                },
            },
        ),
    )

source_activity_rows = []
target_activity_rows = []
source_activity_by_id = {
    row["id"]: row
    for row in source["activities"]
}
source_activity_type_by_id = {
    row["id"]: row
    for row in source["activity_types"]
}
for row in source["activities"]:
    activity = activity_map[row["id"]]
    collaboration_fallback_note = (
        (row["note"] or "")
        + "<p><em>The source activity had no assignee. It is assigned to Roger, "
        "its source creator, for review.</em></p>"
    )
    expected_note = row["note"]
    if (
        not row["user_id"]
        and row["res_model"] == "project.task"
        and str(activity.note or "") == collaboration_fallback_note
    ):
        # The terminal Collaboration stage gives otherwise ownerless Project
        # work to its source creator and records that exact semantic
        # translation in the note. A resumed final-state Project validation
        # must accept only this documented value, not arbitrary note drift.
        expected_note = collaboration_fallback_note
    expected_activity_type = source["xmlids"].get(
        ("mail.activity.type", row["activity_type_id"]),
    ) or row["activity_type_id"]
    actual_activity_type = (
        external_id(activity.activity_type_id)
        or source_id(activity.activity_type_id)
    )
    source_activity_type = source_activity_type_by_id[row["activity_type_id"]]
    semantic_activity_type = (
        not actual_activity_type
        and source_text(source_activity_type["name"])
        == source_text(activity.activity_type_id.name)
        and (source_activity_type["res_model"] or False)
        == (activity.activity_type_id.res_model or False)
        and source_activity_type["category"]
        == activity.activity_type_id.category
    )
    if semantic_activity_type:
        # Collaboration is source-wide and may replace a Project importer's
        # generic traced type with an exact native model-specific type. Keep
        # parity strict on name, model and category while representing that
        # intentional semantic recompute identically on both sides.
        expected_activity_type = f"semantic:{row['activity_type_id']}"
        actual_activity_type = expected_activity_type
    source_activity_rows.append(
        (
            row["id"],
            {
                "model": row["res_model"],
                "record": row["res_id"],
                "type": expected_activity_type,
                "user": (
                    target_user_login[row["user_id"]]
                    if row["user_id"]
                    else "documented-fallback"
                ),
                "summary": row["summary"],
                "deadline": row["date_deadline"],
                "done": row["date_done"],
                "note": expected_note,
                "feedback": row["feedback"],
                "automated": row["automated"],
                "active": bool(row["active"]),
            },
        ),
    )
    target_activity_rows.append(
        (
            row["id"],
            {
                "model": activity.res_model,
                "record": source_id(
                    env[activity.res_model].sudo().browse(activity.res_id),
                ),
                "type": actual_activity_type,
                "user": (
                    activity.user_id.login
                    if source_activity_by_id[row["id"]]["user_id"]
                    else "documented-fallback"
                ),
                "summary": activity.summary,
                "deadline": activity.date_deadline,
                "done": activity.date_done,
                "note": activity.note,
                "feedback": activity.feedback,
                "automated": activity.automated,
                "active": activity.active,
            },
        ),
    )

source_follower_rows = []
target_follower_rows = []
for row in source["followers"]:
    follower = follower_map[row["id"]]
    source_follower_rows.append(
        (
            row["id"],
            {
                "model": row["res_model"],
                "record": row["res_id"],
                "partner": row["partner_id"],
                "subtypes": sorted(
                    source["xmlids"].get(
                        ("mail.message.subtype", subtype_id),
                    )
                    for subtype_id in expected_follower_subtypes[row["id"]]
                ),
            },
        ),
    )
    target_follower_rows.append(
        (
            row["id"],
            {
                "model": follower.res_model,
                "record": source_id(
                    env[follower.res_model].sudo().browse(follower.res_id),
                ),
                "partner": source_id(follower.partner_id),
                "subtypes": sorted(
                    external_id(subtype)
                    for subtype in follower.subtype_ids
                ),
            },
        ),
    )

source_attachment_rows = []
target_attachment_rows = []
for row in source["attachments"]:
    attachment = attachment_map[row["id"]]
    source_attachment_rows.append(
        (
            row["id"],
            {
                "name": row["name"],
                "model": row["res_model"],
                "record": row["res_id"],
                "checksum": row["checksum"],
                "mimetype": row["mimetype"],
                "description": row["description"],
                "public": row["public"],
            },
        ),
    )
    target_attachment_rows.append(
        (
            row["id"],
            {
                "name": attachment.name,
                "model": attachment.res_model,
                "record": source_id(
                    env[attachment.res_model]
                    .sudo()
                    .browse(attachment.res_id),
                ),
                "checksum": attachment.checksum,
                "mimetype": attachment.mimetype,
                "description": attachment.description,
                "public": attachment.public,
            },
        ),
    )

parity_hashes = {
    "projects": (
        canonical_digest(source_project_rows),
        canonical_digest(target_project_rows),
    ),
    "tasks": (
        canonical_digest(source_task_rows),
        canonical_digest(target_task_rows),
    ),
    "messages": (
        canonical_digest(source_message_rows),
        canonical_digest(target_message_rows),
    ),
    "tracking_values": (
        canonical_digest(source_tracking_rows),
        canonical_digest(target_tracking_rows),
    ),
    "activities": (
        canonical_digest(source_activity_rows),
        canonical_digest(target_activity_rows),
    ),
    "followers": (
        canonical_digest(source_follower_rows),
        canonical_digest(target_follower_rows),
    ),
    "attachments": (
        canonical_digest(source_attachment_rows),
        canonical_digest(target_attachment_rows),
    ),
}
parity_mismatches = {
    area: {"source": values[0], "target": values[1]}
    for area, values in parity_hashes.items()
    if values[0] != values[1]
}


def mismatch_examples(source_rows, target_rows, *, limit=5):
    source_by_id = dict(source_rows)
    target_by_id = dict(target_rows)
    examples = []
    for record_id in sorted(set(source_by_id) | set(target_by_id)):
        expected = normalized(source_by_id.get(record_id))
        actual = normalized(target_by_id.get(record_id))
        if expected == actual:
            continue
        fields = sorted(set(expected or {}) | set(actual or {}))
        examples.append(
            {
                "source_id": record_id,
                "fields": {
                    field_name: {
                        "source": (expected or {}).get(field_name),
                        "target": (actual or {}).get(field_name),
                    }
                    for field_name in fields
                    if (expected or {}).get(field_name)
                    != (actual or {}).get(field_name)
                },
            },
        )
        if len(examples) == limit:
            break
    return examples


parity_rows = {
    "projects": (source_project_rows, target_project_rows),
    "tasks": (source_task_rows, target_task_rows),
    "messages": (source_message_rows, target_message_rows),
    "tracking_values": (source_tracking_rows, target_tracking_rows),
    "activities": (source_activity_rows, target_activity_rows),
    "followers": (source_follower_rows, target_follower_rows),
    "attachments": (source_attachment_rows, target_attachment_rows),
}
parity_examples = {
    area: mismatch_examples(*parity_rows[area])
    for area in parity_mismatches
}
assert not parity_mismatches, (
    "Source-to-target field parity differs: "
    f"{parity_mismatches}; examples: {parity_examples}"
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
    "source_target_parity_hashes": {
        area: values[0]
        for area, values in parity_hashes.items()
    },
}
assert summary["dependency_links"] == statistics["target"]["dependencies"]
assert summary["active_projects"] + summary["archived_projects"] == len(projects)
assert summary["active_tasks"] + summary["archived_tasks"] == len(tasks)

print(json.dumps(summary, indent=2, sort_keys=True))
