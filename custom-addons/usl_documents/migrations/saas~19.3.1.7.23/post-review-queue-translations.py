from odoo import SUPERUSER_ID, api


def _write_translated_name(record, english, french):
    record.with_context(lang="en_US").write({"name": english})
    if record.env["res.lang"]._lang_get("fr_FR"):
        record.with_context(lang="fr_FR").write({"name": french})


def migrate(cr, version):
    del version
    env = api.Environment(cr, SUPERUSER_ID, {})
    shortcut = env.ref(
        "usl_documents.quick_filter_needs_review",
        raise_if_not_found=False,
    )
    if shortcut:
        _write_translated_name(
            shortcut,
            "Ready for review",
            "Prêt pour vérification",
        )
    attention_view = env.ref(
        "usl_documents.smart_view_needs_review",
        raise_if_not_found=False,
    )
    if attention_view:
        _write_translated_name(
            attention_view,
            "Needs attention",
            "Attention requise",
        )
