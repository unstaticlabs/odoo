from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Repair the old French view term, preserving any local rewording."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    if not env["res.lang"].search_count([("code", "=", "fr_FR")]):
        return
    view = env.ref("rebuild_account_migration.view_company_rebuild_einvoice_readiness_form")
    view.update_field_translations(
        "arch_db",
        {"fr_FR": {"SIRENE ou SORCIÈRE": "SIREN ou SIRET"}},
        source_lang="fr_FR",
    )
