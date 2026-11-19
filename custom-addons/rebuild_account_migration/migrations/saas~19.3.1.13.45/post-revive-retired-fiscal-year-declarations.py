from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Revive the declarations the September rollover wrongly retired.

    Until this version the schedule dropped the most recently completed fiscal
    year at the October rollover, and the retirement pass then marked its still
    unreviewed declarations "not applicable". The schedule now keeps that year
    until it is locked, and a sync puts an unreviewed "not applicable" row of an
    unlocked period back to preparation. This re-syncs every profiled company
    once, so the rows already retired are revived on upgrade without a manual
    step. Filed, paid and archived rows, rows with a recorded review and rows of
    a locked period are left as they are. The sync is idempotent: a second run
    changes nothing.
    """
    del version
    env = api.Environment(cr, SUPERUSER_ID, {})
    env["rebuild.account.declaration"]._sync_all_profiled_companies()
