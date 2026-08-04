from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    del version
    env = api.Environment(cr, SUPERUSER_ID, {})
    filters = env["ir.filters"].search([("model_id", "=", "usl.document")])
    for native_filter in filters:
        values = {}
        if "paperless_created" in (native_filter.domain or ""):
            values["domain"] = native_filter.domain.replace(
                "paperless_created",
                "archive_added_at",
            )
        if "paperless_created" in (native_filter.context or ""):
            values["context"] = native_filter.context.replace(
                "paperless_created",
                "archive_added_at",
            )
        if "paperless_created" in (native_filter.sort or ""):
            values["sort"] = native_filter.sort.replace(
                "paperless_created",
                "archive_added_at",
            )
        if values:
            native_filter.write(values)

    shortcuts = env["usl.document.quick.filter"].search([])
    for shortcut in shortcuts:
        values = {}
        for field_name in ("group_by_1", "group_by_2", "group_by_3"):
            if shortcut[field_name] == "paperless_created:month":
                values[field_name] = "archive_added_at:month"
        for field_name in ("sort_by_1", "sort_by_2", "sort_by_3"):
            if shortcut[field_name] == "paperless_created":
                values[field_name] = "archive_added_at"
        if values:
            shortcut.write(values)
