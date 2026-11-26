"""Configure reviewed existing boards after upgrading usl_project.

From Odoo shell, use USL_TASK_INBOX_CONFIG for the JSON mapping:
{"shared_later_id": 1, "projects": {
  "10": {"inbox": 2, "first": 3, "resequence": {"3": [0, 1]}},
  "11": {"inbox": null, "first": 4, "later": 5}
}}

inbox=null adds a local Inbox. first is the reviewed native stage ID (null for
an empty board). resequence explicitly approves an old and new local sequence.
later overrides the shared source. The default is a read-only preview.
USL_TASK_INBOX_APPLY=1 commits only after post-configuration verification.
"""

import json
import os

from odoo import Command, fields


def _preserved_rows(env):
    env.flush_all()
    env.cr.execute("""
        SELECT (SELECT md5(string_agg(to_jsonb(t)::text, '' ORDER BY t.id))
                  FROM project_task t),
               (SELECT md5(string_agg(to_jsonb(l)::text, '' ORDER BY l.id))
                  FROM project_task_user_rel l),
               (SELECT md5(string_agg(to_jsonb(p)::text, '' ORDER BY p.id))
                  FROM project_project p),
               (SELECT md5(string_agg(to_jsonb(s)::text, '' ORDER BY s.id))
                  FROM project_task_type s WHERE s.user_id IS NOT NULL),
               (SELECT md5(string_agg(tz, ',' ORDER BY id)) FROM res_company)
    """)
    return env.cr.fetchone()


def configure(env, config, *, apply=False):
    if not env.su:
        raise ValueError("Run the reviewed rollout as an administrator.")
    Project = env["project.project"].with_context(active_test=True)
    Stage = env["project.task.type"].with_context(active_test=True, lang="en_US", default_user_id=False)
    Task = env["project.task"]
    shared = Stage.browse(config["shared_later_id"]).exists()
    rows = {int(key): value for key, value in config["projects"].items()}
    projects = Project.search([("is_template", "=", False)])
    if set(projects.ids) != set(rows) or not shared or not shared.active or shared.user_id or shared.fold:
        raise ValueError("Project coverage or the shared Later stage differs from the reviewed configuration.")
    if shared.usl_reactivation_role != "later" or not set(shared.project_ids.ids) <= set(projects.ids):
        raise ValueError("The shared Later stage has an unexpected role or project.")
    if apply:
        projects.lock_for_update()
        stages = projects.type_ids | shared
        stages.sorted("id").lock_for_update()
        before = _preserved_rows(env)

    now = fields.Datetime.now()
    changes = []
    collected = []
    for project in projects:
        row = rows[project.id]
        stages = project.type_ids.filtered("active")
        first = project._get_default_task_stage()
        inbox = Stage.browse(row["inbox"]) if row["inbox"] else stages.filtered(
            lambda stage: stage.with_context(lang="en_US").name == "Inbox" and stage == first
            and stage.project_ids == project,
        )
        later = Stage.browse(row.get("later", shared.id))
        if not later.exists() or not later.active or later.user_id or (later != shared and later not in stages):
            raise ValueError(f"Unexpected Later stage on project {project.id}.")
        if later.usl_reactivation_role not in (False, "later") or stages.filtered(
            lambda stage: stage != later and stage.usl_reactivation_role == "later",
        ):
            raise ValueError(f"Unexpected additional Later stage on project {project.id}.")
        if inbox and (inbox not in stages or inbox.user_id or inbox.fold or inbox == later):
            raise ValueError(f"Unexpected Inbox on project {project.id}.")
        if (first.id or None) != row["first"] and first != inbox:
            raise ValueError(f"The native intake stage has changed on project {project.id}.")
        sequence_changes = {}
        for key, (old, new) in row.get("resequence", {}).items():
            stage = Stage.browse(int(key))
            if stage not in stages or stage.project_ids != project or stage.sequence not in (old, new):
                raise ValueError(f"The reviewed stage order changed on project {project.id}.")
            sequence_changes[stage.id] = new
        # Validate the final order before making any changes, including a shared
        # source whose sequence can precede intake on other project boards.
        board = stages | later
        if inbox and min(board, key=lambda stage: (
            stage.fold, sequence_changes.get(stage.id, stage.sequence), stage.id,
        )) != inbox:
            raise ValueError(f"The proposed Inbox would not be the native intake on project {project.id}.")
        tasks = Task.search([
            ("project_id", "=", project.id), ("stage_id", "=", later.id),
            ("state", "not in", ("1_done", "1_canceled")), ("is_template", "=", False),
        ])
        due = sum(task._usl_is_due_in_timezone(project.company_id.tz, now) for task in tasks)
        changes.append({
            "project_id": project.id, "inbox_id": inbox.id or None, "later_id": later.id,
            "open_on_later": len(tasks), "due_on_later": due, "add_later": later not in stages,
            "resequence": sequence_changes,
        })
        collected.append((project, inbox, later, board, sequence_changes))

    if apply:
        for project, inbox, later, board, sequence_changes in collected:
            for stage_id, sequence in sequence_changes.items():
                stage = Stage.browse(stage_id)
                if stage.sequence != sequence:
                    stage.sequence = sequence
            if not inbox:
                inbox = Stage.create({
                    "name": "Inbox", "user_id": False, "project_ids": [Command.link(project.id)],
                    "sequence": min(board.mapped("sequence"), default=0) - 1,
                })
                if "fr_FR" in dict(env["res.lang"].get_installed()):
                    inbox.update_field_translations("name", {"fr_FR": "Boîte de réception"})
            if later.usl_reactivation_role != "later":
                later.usl_reactivation_role = "later"
            if project not in later.project_ids:
                later.project_ids = [Command.link(project.id)]
            if project._get_default_task_stage() != inbox or later not in project.type_ids:
                raise ValueError(f"Post-configuration validation failed on project {project.id}.")
        if _preserved_rows(env) != before:
            raise ValueError("Task, personal classification, project or company data changed.")

    links = env["project.task.stage.personal"].search([
        ("stage_id.usl_reactivation_role", "=", "later"), ("stage_id.active", "=", True),
        ("user_id.active", "=", True), ("user_id.share", "=", False),
        ("task_id.active", "=", True), ("task_id.state", "not in", ("1_done", "1_canceled")),
    ])
    personal_inboxes = Stage._usl_personal_inboxes(links.user_id)
    return {
        "projects": changes,
        "personal_due": sum(
            link.task_id._usl_is_due_for_user(link.user_id, now) for link in links
            if link.stage_id.user_id == link.user_id and link.user_id in link.task_id.user_ids
            and personal_inboxes.get(link.user_id.id) and personal_inboxes[link.user_id.id] != link.stage_id
        ),
    }


if "env" in globals():
    apply = os.environ.get("USL_TASK_INBOX_APPLY") == "1"
    outcome = configure(env, json.loads(os.environ["USL_TASK_INBOX_CONFIG"]), apply=apply)
    if apply:
        env.cr.commit()
    print(json.dumps(outcome, sort_keys=True))
