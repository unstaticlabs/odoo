from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    del version
    env = api.Environment(cr, SUPERUSER_ID, {})
    link_model = env["usl.document.link"].sudo()
    payslip_links = link_model.search(
        [
            ("res_model", "=", "usl.tese.payslip"),
            ("active", "=", True),
        ],
    )
    if not payslip_links:
        return

    payslips = env["usl.tese.payslip"].sudo().browse(
        payslip_links.mapped("res_id"),
    ).exists()
    payslips_by_id = {payslip.id: payslip for payslip in payslips}
    employee_ids = payslips.mapped("employee_id").ids
    existing_links = link_model.search(
        [
            ("document_id", "in", payslip_links.mapped("document_id").ids),
            ("res_model", "=", "hr.employee"),
            ("res_id", "in", employee_ids),
        ],
    )
    existing_by_key = {
        (link.document_id.id, link.res_id): link for link in existing_links
    }
    values_list = []
    touched_document_ids = set()
    reactivate_ids = []
    promote_ids = []
    for payslip_link in payslip_links:
        payslip = payslips_by_id.get(payslip_link.res_id)
        if not payslip or not payslip.employee_id:
            continue
        key = (payslip_link.document_id.id, payslip.employee_id.id)
        employee_link = existing_by_key.get(key)
        if employee_link:
            if not employee_link.active:
                reactivate_ids.append(employee_link.id)
                touched_document_ids.add(employee_link.document_id.id)
            if employee_link.document_role == "background":
                promote_ids.append(employee_link.id)
                touched_document_ids.add(employee_link.document_id.id)
            continue
        values_list.append(
            {
                "document_id": payslip_link.document_id.id,
                "res_model": "hr.employee",
                "res_id": payslip.employee_id.id,
                "record_name": payslip.employee_id.display_name,
                "company_id": payslip_link.company_id.id,
                "linked_by_id": payslip_link.linked_by_id.id or SUPERUSER_ID,
                "version_id": payslip_link.version_id or False,
                "archive_mode": payslip_link.archive_mode,
                "policy_role": "library",
                "document_role": "library",
                "attachment_origin": "backfill",
                "policy_reason": "tese_employee_library",
                "active": True,
            },
        )
        touched_document_ids.add(payslip_link.document_id.id)
        existing_by_key[key] = True

    if reactivate_ids:
        cr.execute(
            "UPDATE usl_document_link SET active = TRUE WHERE id IN %s",
            (tuple(reactivate_ids),),
        )
    if promote_ids:
        cr.execute(
            """
            UPDATE usl_document_link
               SET document_role = 'library'
             WHERE id IN %s
               AND document_role = 'background'
            """,
            (tuple(promote_ids),),
        )
    if values_list:
        link_model.with_context(usl_documents_link_policy_write=True).create(
            values_list,
        )
    if not touched_document_ids:
        return

    env.invalidate_all()
    documents = env["usl.document"].sudo().browse(touched_document_ids).exists()
    access_before = {
        document.id: set(document.permitted_user_ids.ids) for document in documents
    }
    documents._recompute_linked_record_access(sync_permissions=False)
    access_changed = documents.filtered(
        lambda document: access_before[document.id]
        != set(document.permitted_user_ids.ids),
    )
    if access_changed:
        access_changed.with_context(
            usl_documents_cache_write=True,
            skip_permission_invalidation=True,
        ).write(
            {
                "permission_sync_state": "pending",
                "permission_sync_error": False,
                "permission_checked_at": False,
            },
        )
