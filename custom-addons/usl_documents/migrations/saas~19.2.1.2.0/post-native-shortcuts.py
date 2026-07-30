from odoo import SUPERUSER_ID, api


def _related_ids(cr, table, shortcut_id, value_column):
    cr.execute(
        f"""
        SELECT {value_column}
          FROM {table}
         WHERE filter_id = %s
         ORDER BY {value_column}
        """,
        [shortcut_id],
    )
    return [row[0] for row in cr.fetchall()]


def _legacy_definition(cr, row):
    (
        shortcut_id,
        key,
        kind,
        filter_type,
        days,
        confidentiality,
        field_name,
    ) = row
    filter_type = {
        "my_uploads": "my_uploads",
        "unlinked": "unlinked",
        "needs_review": "needs_review",
        "last_30_days": "recent",
    }.get(key, filter_type)
    context = {}
    domain = []
    if kind == "group":
        if field_name:
            context["group_by"] = [field_name]
    elif filter_type == "my_uploads":
        return "[('submitted_by_id', '=', uid)]", context
    elif filter_type == "unlinked":
        domain = [("has_linked_record", "=", False)]
    elif filter_type == "linked":
        domain = [("has_linked_record", "=", True)]
    elif filter_type == "needs_review":
        domain = [("review_state", "=", "needs_attention")]
    elif filter_type == "recent":
        return (
            "[('paperless_created', '>=', "
            "(context_today() - relativedelta(days=%d)).strftime('%%Y-%%m-%%d'))]"
            % max(1, days or 30),
            context,
        )
    elif filter_type == "accounting":
        domain = [("accounting_evidence", "=", True)]
    elif filter_type == "tags":
        domain = [
            (
                "tag_ids",
                "in",
                _related_ids(
                    cr,
                    "usl_document_quick_filter_tag_rel",
                    shortcut_id,
                    "tag_id",
                ),
            ),
        ]
    elif filter_type == "correspondents":
        domain = [
            (
                "correspondent_id",
                "in",
                _related_ids(
                    cr,
                    "usl_document_quick_filter_correspondent_rel",
                    shortcut_id,
                    "correspondent_id",
                ),
            ),
        ]
    elif filter_type == "document_types":
        domain = [
            (
                "document_type_id",
                "in",
                _related_ids(
                    cr,
                    "usl_document_quick_filter_type_rel",
                    shortcut_id,
                    "document_type_id",
                ),
            ),
        ]
    elif filter_type == "privacy":
        domain = [("confidentiality", "=", confidentiality or "internal")]
    return repr(domain), context


def migrate(cr, version):
    del version
    env = api.Environment(cr, SUPERUSER_ID, {})
    cr.execute(
        """
        SELECT id, key, kind, filter_type, days, confidentiality, field_name
          FROM usl_document_quick_filter
         WHERE ir_filter_id IS NULL
         ORDER BY id
        """,
    )
    legacy_shortcuts = cr.fetchall()
    action = env.ref("usl_documents.action_documents_workspace")
    shortcut_model = env["usl.document.quick.filter"]
    seeded_filters = {
        "my_uploads": "usl_documents.ir_filter_quick_my_uploads",
        "unlinked": "usl_documents.ir_filter_quick_unlinked",
        "needs_review": "usl_documents.ir_filter_quick_needs_review",
        "last_30_days": "usl_documents.ir_filter_quick_last_30_days",
        "group_company": "usl_documents.ir_filter_quick_group_company",
        "group_correspondent": (
            "usl_documents.ir_filter_quick_group_correspondent"
        ),
        "group_document_type": (
            "usl_documents.ir_filter_quick_group_document_type"
        ),
        "group_employee": "usl_documents.ir_filter_quick_group_employee",
        "group_document_month": (
            "usl_documents.ir_filter_quick_group_document_month"
        ),
    }
    for row in legacy_shortcuts:
        shortcut = shortcut_model.browse(row[0]).exists()
        if not shortcut:
            continue
        domain, context = _legacy_definition(cr, row)
        values = {
            "name": shortcut.name,
            "model_id": "usl.document",
            "action_id": action.id,
            "domain": domain,
            "context": repr(context),
            "sort": "[]",
            "user_ids": [],
        }
        xmlid = seeded_filters.get(row[1])
        native_filter = (
            env.ref(xmlid, raise_if_not_found=False)
            if xmlid
            else env["ir.filters"]
        )
        if native_filter:
            native_filter.write(values)
        else:
            native_filter = env["ir.filters"].create(values)
        shortcut.ir_filter_id = native_filter
