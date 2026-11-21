from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Revive the declarations the September rollover wrongly retired.

    Until this version the schedule dropped the most recently completed fiscal
    year at the October rollover, and the retirement pass then marked its still
    unreviewed declarations "not applicable". The schedule now keeps that year
    until it is locked, and a sync puts an unreviewed "not applicable" row of an
    unlocked period back to preparation. This re-syncs every profiled company
    once, so the rows already retired are revived on upgrade without a manual
    step. The calendar retains existing locked or completed declarations and
    exclusions backed by the latest recorded non-applicability decision. This
    also repairs open filings previously marked ready by that decision. For
    other reviewed open filings, rule facts and automatic checks can refresh while the
    recorded decision retains its evidence and conclusion.
    """
    del version
    env = api.Environment(cr, SUPERUSER_ID, {})
    declarations = env["rebuild.account.declaration"]
    declarations._sync_all_profiled_companies()
    # A company can disable its calendar after recording a review. Repair its
    # existing filing without generating a new schedule.
    inactive_profiles = env["rebuild.account.assurance.decision"].search([
        ("gate", "=", "declaration_review"), ("state", "in", ["recorded", "superseded"]),
        ("conclusion", "=", "not_applicable"),
        ("company_id.rebuild_declaration_profile_active", "=", False),
    ]).company_id
    for company in inactive_profiles:
        declarations._sync_recorded_filing_exclusions(company)
