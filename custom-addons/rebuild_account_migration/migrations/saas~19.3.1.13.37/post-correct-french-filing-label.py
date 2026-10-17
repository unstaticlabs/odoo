from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Correct the shipped French label without overwriting custom translations."""
    del version
    env = api.Environment(cr, SUPERUSER_ID, {})
    if not env["res.lang"]._lang_get("fr_FR"):
        return
    for xmlid in (
        "selection__rebuild_account_declaration__status__filed",
        "selection__rebuild_account_declaration__filing_status__filed",
        "selection__rebuild_account_overview__next_declaration_status__filed",
    ):
        selection = env.ref("rebuild_account_migration." + xmlid).with_context(lang="fr_FR")
        if selection.name == "Classé en externe":
            selection.write({"name": "Déposée en externe"})
