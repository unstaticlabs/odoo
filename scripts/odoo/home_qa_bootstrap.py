"""Idempotent, isolated browser personas for the Home cockpit QA profile."""

# ruff: noqa: F821, T201 - Odoo shell supplies ``env`` and this operator
# bootstrap prints its concise completion evidence.

from datetime import timedelta

from odoo import Command, fields

# The focused Home profile intentionally does not start Pocket ID. Keep its
# synthetic personas reachable through Odoo's local login without weakening
# the product database or the full QA profile's SSO-only policy.
env["ir.config_parameter"].sudo().set_str(
    "usl_pocketid.login_policy", "standard",
)


def ref(xmlid):
    return env.ref(xmlid, raise_if_not_found=False)


def ensure_user(login, name, groups, companies):
    User = env["res.users"].with_context(active_test=False)
    user = User.search([("login", "=", login)], limit=1)
    vals = {
        "name": name,
        "login": login,
        "email": f"{login}@home-qa.invalid",
        "active": True,
        "company_id": companies[0].id,
        "company_ids": [Command.set(companies.ids)],
        "group_ids": [Command.set([group.id for group in groups if group])],
        "action_id": ref("usl_home.action_usl_home").id,
    }
    if user:
        user.write(vals)
    else:
        user = User.create(vals)
    user.sudo().write({"password": "homeqa"})
    return user


main_company = env.company
second_company = env["res.company"].search(
    [("id", "!=", main_company.id)], order="id", limit=1,
)
if not second_company:
    second_company = env["res.company"].create({"name": "USL Home QA Company"})
companies = main_company | second_company

group_user = ref("base.group_user")
group_system = ref("base.group_system")
group_project_user = ref("project.group_project_user")
group_project_manager = ref("project.group_project_manager")
group_account_manager = ref("account.group_account_manager")
group_account_reviewer = ref(
    "rebuild_account_migration.group_rebuild_accountant_reviewer",
)

founder = ensure_user(
    "home.qa.founder",
    "Home QA Founder",
    group_user | group_system | group_project_manager | group_account_manager,
    companies,
)
operations = ensure_user(
    "home.qa.operations",
    "Home QA Operations",
    group_user | group_project_user,
    companies,
)
accounting = ensure_user(
    "home.qa.accounting",
    "Home QA Accounting Reviewer",
    group_user | group_account_reviewer,
    companies,
)
restricted = ensure_user(
    "home.qa.restricted",
    "Home QA Restricted",
    group_user,
    companies,
)

Project = env["project.project"]
Task = env["project.task"]
Stage = env["project.task.type"]
Tag = env["project.tags"]

operations_project = Project.search([("name", "=", "Home QA Operations")], limit=1)
if operations_project:
    operations_project.task_ids.unlink()
else:
    operations_project = Project.create({"name": "Home QA Operations"})

ai_project = Project.search([("name", "=", "Home QA AI Pipeline")], limit=1)
if ai_project:
    ai_project.task_ids.unlink()
else:
    ai_project = Project.create({"name": "Home QA AI Pipeline"})


def ensure_stage(name, project, sequence, fold=False):
    stage = Stage.search(
        [("name", "=", name), ("project_ids", "in", project.ids)], limit=1,
    )
    if not stage:
        stage = Stage.create(
            {
                "name": name,
                "sequence": sequence,
                "fold": fold,
                "project_ids": [Command.set(project.ids)],
            },
        )
    else:
        stage.project_ids = [Command.link(project_id) for project_id in project.ids]
    return stage


inbox = ensure_stage("Inbox", operations_project, 10)
in_progress = ensure_stage("In Progress", operations_project, 20)
waiting = ensure_stage("Waiting", operations_project, 30)
review = ensure_stage("Review", operations_project | ai_project, 40)
build = ensure_stage("Build", ai_project, 20)


def ensure_tag(name):
    return Tag.search([("name", "=", name)], limit=1) or Tag.create({"name": name})


agent_ready = ensure_tag("Agent Ready")
agent_failed = ensure_tag("Agent Failed")
needs_human = ensure_tag("Needs Human")
human_approved = ensure_tag("Human Approved")
has_pr = ensure_tag("Has PR")
blocked = ensure_tag("Blocked")

today = fields.Date.today()
task_values = [
    ("Prepare operations handoff", inbox, operations, today - timedelta(days=2), "01_in_progress"),
    ("Review supplier evidence", in_progress, founder, today, "02_changes_requested"),
    ("Wait for partner confirmation", waiting, operations, today + timedelta(days=3), "04_waiting_normal"),
    ("Validate launch checklist", review, founder, today + timedelta(days=6), "01_in_progress"),
]
tasks = []
for name, stage, assignee, deadline, state in task_values:
    tasks.append(
        Task.create(
            {
                "name": name,
                "project_id": operations_project.id,
                "stage_id": stage.id,
                "user_ids": [Command.link(assignee.id)],
                "date_deadline": deadline,
                "state": state,
            },
        ),
    )

ai_values = [
    ("Investigate failed document classifier", build, [agent_failed], "01_in_progress"),
    ("Approve reconciliation assistant handoff", review, [needs_human, has_pr], "01_in_progress"),
    ("Resolve blocked release integration", build, [agent_ready, blocked], "01_in_progress"),
    ("Accepted agent output", build, [human_approved], "03_approved"),
]
for offset, (name, stage, tags, state) in enumerate(ai_values):
    Task.create(
        {
            "name": name,
            "project_id": ai_project.id,
            "stage_id": stage.id,
            "user_ids": [Command.link(founder.id)],
            "tag_ids": [Command.set([tag.id for tag in tags])],
            "date_deadline": today + timedelta(days=offset - 1),
            "state": state,
        },
    )

activity_type = ref("mail.mail_activity_data_todo")
for activity in env["mail.activity"].search(
    [("user_id", "=", founder.id), ("summary", "like", "Home QA")],
):
    activity.unlink()
activity_dates = [
    today - timedelta(days=3),
    today - timedelta(days=1),
    today,
    today,
    today + timedelta(days=1),
    today + timedelta(days=2),
    today + timedelta(days=3),
]
for index, deadline in enumerate(activity_dates, start=1):
    env["mail.activity"].create(
        {
            "activity_type_id": activity_type.id,
            "summary": f"Home QA attention {index}",
            "date_deadline": deadline,
            "user_id": founder.id,
            "res_model_id": env["ir.model"]._get_id("project.task"),
            "res_id": tasks[(index - 1) % len(tasks)].id,
        },
    )

env["usl.home.favorite"].sudo().search(
    [("user_id", "in", [founder.id, operations.id, accounting.id, restricted.id])],
).unlink()
env["res.users.settings"]._find_or_create_for_user(founder).write(
    {"usl_home_favorites_initialized": False},
)
env["res.users.settings"]._find_or_create_for_user(operations).write(
    {"usl_home_favorites_initialized": False},
)
env["res.users.settings"]._find_or_create_for_user(accounting).write(
    {"usl_home_favorites_initialized": False},
)
restricted_settings = env["res.users.settings"]._find_or_create_for_user(restricted)
restricted_settings.usl_home_favorites_initialized = True
env["usl.home.favorite"].sudo().create(
    {
        "user_id": restricted.id,
        "name": "Protected Project Destination",
        "target_type": "action",
        "action_id": ref("project.action_view_my_task").id,
    },
)

env.cr.commit()
print("Home QA personas ready: home.qa.founder, home.qa.operations, home.qa.accounting, home.qa.restricted")
