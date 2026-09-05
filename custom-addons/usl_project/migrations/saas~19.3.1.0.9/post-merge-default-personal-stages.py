"""Merge duplicated translated defaults in users' personal task pipelines."""

import unicodedata

from odoo import SUPERUSER_ID, api


DEFAULT_PERSONAL_STAGES = {
    "inbox": {
        "en_US": "Inbox",
        "fr_FR": "Boîte de réception",
    },
    "today": {
        "en_US": "Today",
        "fr_FR": "Aujourd'hui",
    },
    "this_week": {
        "en_US": "This Week",
        "fr_FR": "Cette semaine",
    },
    "this_month": {
        "en_US": "This Month",
        "fr_FR": "Ce mois",
    },
    "later": {
        "en_US": "Later",
        "fr_FR": "Plus tard",
    },
    "done": {
        "en_US": "Done",
        "fr_FR": "Terminé",
    },
    "cancelled": {
        "en_US": "Cancelled",
        "fr_FR": "Annulé",
    },
}


def _normalized_label(label):
    decomposed = unicodedata.normalize("NFKD", label or "")
    return " ".join(
        "".join(character for character in decomposed if not unicodedata.combining(character))
        .casefold()
        .split()
    )


DEFAULT_STAGE_BY_LABEL = {
    _normalized_label(label): key
    for key, translations in DEFAULT_PERSONAL_STAGES.items()
    for label in translations.values()
}


def _default_stage_key(stage):
    labels = {
        stage.with_context(lang=language).name
        for language in ("en_US", "fr_FR")
    }
    keys = {
        DEFAULT_STAGE_BY_LABEL.get(_normalized_label(label))
        for label in labels
    }
    keys.discard(None)
    return keys.pop() if len(keys) == 1 else None


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    Stage = env["project.task.type"].with_context(active_test=False).sudo()
    PersonalStage = env["project.task.stage.personal"].sudo()
    grouped_stages = {}

    for stage in Stage.search([("active", "=", True), ("user_id", "!=", False)]):
        default_key = _default_stage_key(stage)
        if default_key:
            grouped_stages.setdefault((stage.user_id.id, default_key), Stage)
            grouped_stages[(stage.user_id.id, default_key)] |= stage

    for (_user_id, default_key), stages in grouped_stages.items():
        if len(stages) < 2:
            continue

        usage_by_stage = {
            stage.id: PersonalStage.search_count([("stage_id", "=", stage.id)])
            for stage in stages
        }
        canonical = stages.sorted(
            key=lambda stage: (
                -usage_by_stage[stage.id],
                stage.create_date,
                stage.sequence,
                stage.id,
            ),
        )[0]
        duplicates = stages - canonical

        PersonalStage.search([("stage_id", "in", duplicates.ids)]).write(
            {"stage_id": canonical.id},
        )
        for language, label in DEFAULT_PERSONAL_STAGES[default_key].items():
            canonical.with_context(lang=language).write({"name": label})

        # Keep the redundant record as an inactive audit identity. Direct SQL
        # avoids project.task.type.write(), whose project-stage side effect is
        # irrelevant to personal pipeline stages.
        cr.execute(
            """
                UPDATE project_task_type
                   SET active = FALSE,
                       write_date = NOW(),
                       write_uid = %s
                 WHERE id = ANY(%s)
            """,
            (SUPERUSER_ID, duplicates.ids),
        )

    env.invalidate_all()
