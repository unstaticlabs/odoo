"""Validate and apply a reviewed task reactivation rollout from Odoo shell.

Pass USL_TASK_REACTIVATION_ROLLOUT as JSON with company_id, owner_id,
source_stage_id, inbox_by_project (project ID keys, stage ID values),
personal_later_id, detached_project_id, other_personal_stage_ids,
archived_role_stage_id and timezone. The default is a read-only preview.
Set USL_TASK_REACTIVATION_APPLY=1 to commit after checking the same preconditions.
Use a qualified restore to rehearse the rollout before applying it to live data.
"""

import json
import os

from odoo import Command


def configure(env, config, *, apply=False):
    Stage = env["project.task.type"].with_context(active_test=False)
    Task = env["project.task"].with_context(active_test=False)
    Personal = env["project.task.stage.personal"].with_context(active_test=False)
    owner = env["res.users"].browse(config["owner_id"]).exists()
    company = env["res.company"].browse(config["company_id"]).exists()
    inbox_by_project = {int(project_id): stage_id for project_id, stage_id in config["inbox_by_project"].items()}
    projects = env["project.project"].browse(inbox_by_project).exists()
    source = Stage.browse(config["source_stage_id"])
    personal_later = Stage.browse(config["personal_later_id"])
    other_personal = Stage.browse(config["other_personal_stage_ids"])
    archived = Stage.browse(config["archived_role_stage_id"])
    inboxes = Stage.browse(list(inbox_by_project.values()))
    stages = source | personal_later | other_personal | archived | inboxes
    timezone = config["timezone"]

    if (
        not env.su
        or not owner
        or not owner.active
        or owner.share
        or not company
        or not projects
        or set(projects.ids) != set(inbox_by_project)
        or any(not project.active or project.company_id != company for project in projects)
        or config["detached_project_id"] not in projects.ids
        or len(stages.exists()) != len(other_personal) + len(set(inboxes.ids)) + 3
        or company.tz not in {"UTC", timezone}
        or not env["res.lang"].search_count([("code", "=", "fr_FR")])
    ):
        raise ValueError("The rollout identities, company timezone or French locale do not match the reviewed scope.")

    if apply:
        stages.sorted("id").lock_for_update()
        company.lock_for_update()

    if (
        not source.active
        or source.user_id
        or set(source.project_ids.ids) != set(projects.ids)
        or source.usl_reactivation_role not in (False, "later")
        or Personal.search_count([("stage_id", "=", source.id)])
        or Task.search_count([("stage_id", "=", source.id), ("project_id", "not in", projects.ids)])
    ):
        raise ValueError("The shared source is no longer exclusive to the selected project boards.")

    for project in projects:
        inbox = Stage.browse(inbox_by_project[project.id])
        if (
            not inbox.active
            or inbox.user_id
            or inbox not in project.type_ids
            or inbox.usl_reactivation_role not in (False, "inbox")
            or (project.type_ids - inbox).filtered(
                lambda stage: stage.active and stage.usl_reactivation_role == "inbox",
            )
            or Personal.search_count([("stage_id", "=", inbox.id)])
        ):
            raise ValueError(f"The destination for project {project.id} is missing or ambiguous.")

    if (
        archived.active
        or archived.user_id != owner
        or archived.project_ids
        or archived.usl_reactivation_role not in (False, "later")
        or Personal.search_count([("stage_id", "=", archived.id)])
        or Task.search_count([("stage_id", "=", archived.id)])
        or Stage.search_count([
            ("user_id", "=", owner.id),
            ("usl_reactivation_role", "=", "later"),
            ("id", "not in", (archived | personal_later).ids),
        ])
        or not Stage.search_count([
            ("user_id", "=", owner.id),
            ("usl_reactivation_role", "=", "inbox"),
            ("active", "=", True),
        ])
    ):
        raise ValueError("The personal inbox or the archived holder of the Later role has changed.")

    personal_counts = {}
    for stage in personal_later | other_personal:
        permitted_projects = {config["detached_project_id"]} if stage == personal_later else set()
        links = Personal.search([("stage_id", "=", stage.id)])
        permitted_roles = (False, "later") if stage == personal_later else (False,)
        if (
            not stage.active
            or stage.user_id not in (owner, owner.browse())
            or not set(stage.project_ids.ids).issubset(permitted_projects)
            or stage.usl_reactivation_role not in permitted_roles
            or links.user_id - owner
            or (not stage.user_id and not links)
            or Task.search_count([("stage_id", "=", stage.id)])
        ):
            raise ValueError(f"The evidence for restoring ownership of personal stage {stage.id} has changed.")
        personal_counts[stage.id] = len(links)

    report = {
        "project_tasks": [
            {"project_id": project.id, "active": active, "state": state, "count": count}
            for project, active, state, count in Task._read_group(
                [("stage_id", "=", source.id)], ["project_id", "active", "state"], ["__count"],
            )
        ],
        "personal_classifications": personal_counts,
        "company_timezone": [company.tz, timezone],
    }
    if not apply:
        return report

    if archived.usl_reactivation_role:
        archived.usl_reactivation_role = False
    if personal_later.project_ids:
        personal_later.project_ids = [Command.clear()]
    for stage in (personal_later | other_personal).filtered(lambda stage: not stage.user_id):
        stage.user_id = owner
    if personal_later.usl_reactivation_role != "later":
        personal_later.usl_reactivation_role = "later"
    for language, name in (("en_US", "Later"), ("fr_FR", "Plus tard")):
        translated = source.with_context(lang=language)
        if translated.name != name:
            translated.name = name
    if source.usl_reactivation_role != "later":
        source.usl_reactivation_role = "later"
    inboxes.filtered(lambda stage: stage.usl_reactivation_role != "inbox").write(
        {"usl_reactivation_role": "inbox"},
    )
    if company.tz != timezone:
        company.tz = timezone
    return report


if "env" in globals():
    settings = json.loads(os.environ["USL_TASK_REACTIVATION_ROLLOUT"])
    apply = os.environ.get("USL_TASK_REACTIVATION_APPLY") == "1"
    result = configure(env, settings, apply=apply)  # noqa: F821
    if apply:
        env.cr.commit()  # noqa: F821
    print(json.dumps(result, indent=2))
