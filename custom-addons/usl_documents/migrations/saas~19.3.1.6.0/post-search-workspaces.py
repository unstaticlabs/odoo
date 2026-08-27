from odoo import SUPERUSER_ID, Command, api


def migrate(cr, version):
    del version
    env = api.Environment(cr, SUPERUSER_ID, {})
    manager = env.ref("usl_documents.group_documents_manager")
    hr_reader = env.ref("usl_documents.group_documents_hr")
    values_by_xmlid = {
        "usl_documents.smart_view_needs_review": {
            "active": False,
            "sequence": 130,
        },
        "usl_documents.smart_view_recent": {
            "active": False,
            "sequence": 140,
        },
        "usl_documents.smart_view_accounting": {"sequence": 30},
        "usl_documents.smart_view_contracts": {"sequence": 50},
        "usl_documents.smart_view_banking": {"sequence": 60},
        "usl_documents.smart_view_tax": {"sequence": 70},
        "usl_documents.smart_view_hr": {
            "sequence": 80,
            "group_ids": [Command.set((hr_reader | manager).ids)],
        },
        "usl_documents.smart_view_all": {
            "name": "All archived",
            "sequence": 120,
            "group_ids": [Command.set(manager.ids)],
        },
        "usl_documents.smart_view_trash": {"sequence": 110},
    }
    for xmlid, values in values_by_xmlid.items():
        view = env.ref(xmlid, raise_if_not_found=False)
        if view:
            view.write(values)
