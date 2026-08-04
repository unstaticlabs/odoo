from odoo import SUPERUSER_ID, api

SOURCE_TERMS = (
    (
        "Optional default analytic distribution for generated invoice\n"
        "                                and commission lines."
    ),
    "Optional default analytic distribution for generated invoice and commission lines.",
)
FRENCH_TERM = (
    "Répartition analytique par défaut facultative pour les lignes de facture "
    "et de commission générées."
)


def migrate(cr, version):
    del version
    env = api.Environment(cr, SUPERUSER_ID, {})
    view = env.ref("usl_platform_billing.view_platform_billing_platform_form")
    translated_view = view.with_context(lang="fr_FR")
    translated_arch = translated_view.arch_db
    for source_term in SOURCE_TERMS:
        translated_arch = translated_arch.replace(source_term, FRENCH_TERM)
    if translated_arch != translated_view.arch_db:
        translated_view.write({"arch_db": translated_arch})
