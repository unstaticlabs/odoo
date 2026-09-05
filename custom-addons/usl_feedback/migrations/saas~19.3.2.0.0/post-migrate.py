from odoo import SUPERUSER_ID, api

STAGE_NAMES = {
    "stage_feedback_new": ("Inbox", "Boîte de réception"),
    "stage_feedback_triaged": ("Triage", "Triage"),
    "stage_feedback_planned": ("Shaping", "Cadrage"),
    "stage_feedback_in_progress": ("Build", "Réalisation"),
    "stage_feedback_ready_to_verify": ("Review", "Vérifier"),
    "stage_feedback_done": ("Release", "Livraison"),
    "stage_feedback_declined": ("Icebox", "En réserve"),
}


def migrate(cr, version):
    del version
    env = api.Environment(cr, SUPERUSER_ID, {})
    project = env.ref("usl_feedback.project_product_feedback", raise_if_not_found=False)
    if not project:
        return

    inbox = env.ref("usl_feedback.stage_feedback_new")
    env.flush_all()
    cr.execute(
        """
            UPDATE project_task AS task
               SET usl_feedback_company_id = COALESCE(
                       task.usl_feedback_company_id,
                       task.company_id,
                       reporter.company_id,
                       %s
                   ),
                   company_id = NULL,
                   usl_feedback_agent_state = COALESCE(
                       task.usl_feedback_agent_state,
                       CASE WHEN task.stage_id = %s THEN 'waiting' ELSE 'triaged' END
                   )
              FROM res_users AS reporter
             WHERE task.project_id = %s
               AND reporter.id = task.usl_feedback_reporter_id
        """,
        [env.company.id, inbox.id, project.id],
    )
    tasks = env["project.task"].sudo().with_context(active_test=False).search(
        [("project_id", "=", project.id)],
    )
    tasks.invalidate_recordset(
        ["company_id", "usl_feedback_company_id", "usl_feedback_agent_state"],
        flush=False,
    )

    project.write(
        {
            "privacy_visibility": "employees",
            "company_id": False,
            "user_id": False,
            "usl_feedback_project": True,
        },
    )
    for xmlid in ("bug", "improvement", "question", "ux"):
        tag = env.ref(f"usl_feedback.tag_feedback_{xmlid}", raise_if_not_found=False)
        if tag:
            tag.usl_feedback_tag = True
    for xmlid, (name, name_fr) in STAGE_NAMES.items():
        stage = env.ref(f"usl_feedback.{xmlid}", raise_if_not_found=False)
        if stage:
            stage.update_field_translations(
                "name",
                {"en_US": name, "fr_FR": name_fr},
            )

    obsolete_xmlids = (
        "action_feedback_submission",
        "action_my_feedback",
        "view_feedback_submission_form",
        "view_feedback_task_reporter_list",
        "view_feedback_task_reporter_form",
        "view_feedback_task_maintainer_list",
        "view_feedback_task_maintainer_form",
        "rule_feedback_project_boundary",
        "rule_feedback_task_boundary",
    )
    for xmlid in obsolete_xmlids:
        record = env.ref(f"usl_feedback.{xmlid}", raise_if_not_found=False)
        if record:
            record.unlink()
