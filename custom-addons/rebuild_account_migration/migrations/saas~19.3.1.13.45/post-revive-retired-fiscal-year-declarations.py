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
    explicit exclusions backed by a recorded review. For other reviewed open
    filings, current rule facts and automatic checks can refresh while the
    recorded decision retains its evidence and conclusion.
    """
    del version
    env = api.Environment(cr, SUPERUSER_ID, {})
    env["rebuild.account.declaration"]._sync_all_profiled_companies()
