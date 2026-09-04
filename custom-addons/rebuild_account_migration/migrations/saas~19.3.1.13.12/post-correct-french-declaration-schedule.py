from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Reconcile period-aware French obligations without replaying migration."""
    del version
    env = api.Environment(cr, SUPERUSER_ID, {})
    Rule = env["rebuild.account.declaration.rule"].sudo().with_context(
        accounting_definition_seed=True,
    )

    Rule.search([("code", "=", "FR_2571")]).write({
        "trigger_kind": "not_first_fiscal_year",
        "deadline_rule": "is_instalments",
        "conditional": True,
        "payment_required": "conditional",
    })
    thresholds = {
        "FR_DAS2": (2400.0, 0.0),
        "FR_CVAE_1330": (152500.0, 0.0),
        "FR_CVAE_1329_AC": (500000.0, 1500.0),
        "FR_CFE_ACOMPTE": (3000.0, 0.0),
    }
    for code, (primary, secondary) in thresholds.items():
        Rule.search([("code", "=", code)]).write({
            "threshold_amount": primary,
            "secondary_threshold_amount": secondary,
        })

    cr.execute(
        """
        UPDATE rebuild_account_declaration
           SET filing_status = CASE filing_status
               WHEN 'not_started' THEN 'not_open'
               WHEN 'portal_draft' THEN 'ready'
               WHEN 'submitted' THEN 'filed'
               ELSE filing_status
           END
         WHERE filing_status IN ('not_started', 'portal_draft', 'submitted')
        """,
    )
    cr.execute(
        """
        UPDATE rebuild_account_declaration
           SET preparation_status = CASE
               WHEN applicability = 'not_applicable' THEN 'not_required'
               WHEN status IN ('ready_to_file', 'filed', 'paid', 'archived')
                    OR review_status IN ('accepted', 'accepted_with_difference')
                   THEN 'reviewed'
               WHEN status IN ('internal_review', 'accountant_review', 'accountant_reviewed')
                   THEN 'ready_for_review'
               ELSE 'missing_data'
           END
        """,
    )

    Rule._ensure_governance_metadata()
    declarations = env["rebuild.account.declaration"]._sync_all_profiled_companies()
    open_declarations = declarations.filtered(
        lambda declaration: declaration.status not in {"filed", "paid", "archived"},
    )
    for declaration in open_declarations:
        declaration.definition_snapshot = declaration.rule_id._definition_snapshot()
