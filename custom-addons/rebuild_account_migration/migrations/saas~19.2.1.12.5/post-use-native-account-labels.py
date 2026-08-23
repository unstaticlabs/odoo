from odoo import SUPERUSER_ID, api
from odoo.exceptions import UserError

ENGLISH_LABELS = {
    "281540": "Depreciation of industrial equipment",
    "281830": "Depreciation of office and IT equipment",
    "511100": "Platform transfers receivable — Etsy",
    "627100": "Bank charges",
    "631200": "Apprenticeship tax",
    "768000": "Other financial income",
}


def migrate(cr, version):
    del version
    env = api.Environment(cr, SUPERUSER_ID, {})
    Presentation = env[
        "rebuild.account.report.account.presentation"
    ].with_context(active_test=False)
    presentations = Presentation.search([("active", "=", True)])
    scoped = presentations.filtered(
        lambda presentation: presentation.company_id
        or presentation.report_type,
    )
    if scoped:
        message = (
            "Account-label consolidation cannot silently convert company- "
            "or report-specific aliases. Review the legacy records first."
        )
        raise UserError(message)

    Account = env["account.account"].sudo().with_context(active_test=False)
    companies = (
        env["res.company"]
        .sudo()
        .with_context(active_test=False)
        .search([])
    )
    for presentation in presentations:
        accounts = Account.browse()
        for company in companies:
            accounts |= Account.with_company(company).search([
                ("code", "=", presentation.account_code),
                ("company_ids", "in", company.id),
            ])
        translations = {
            "en_US": ENGLISH_LABELS.get(
                presentation.account_code,
                presentation.display_label,
            ),
            "fr_FR": presentation.with_context(lang="fr_FR").display_label,
        }
        for account in accounts:
            account.update_field_translations("name", translations)

    presentations.write({"active": False})
