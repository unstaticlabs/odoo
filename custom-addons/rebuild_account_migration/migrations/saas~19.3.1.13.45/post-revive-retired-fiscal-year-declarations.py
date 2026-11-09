from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Revive the declarations the September rollover wrongly retired.

    Until this version the schedule dropped the most recently completed fiscal
    year at the October rollover, and the retirement pass then marked its still
    unreviewed declarations "not applicable". The schedule now always keeps that year,
    and a sync puts a "not applicable" row without a recorded review back to
    preparation, whether or not a lock covers its year. This re-syncs every
    profiled company once, so the rows already retired are revived on upgrade
    without a manual step. Filed, paid and archived rows and rows with a
    recorded review are left as they are, and no lock date is read for writing
    or changed. The sync is idempotent: a second run changes nothing.
    """
    del version
    env = api.Environment(cr, SUPERUSER_ID, {})
    env["rebuild.account.declaration"]._sync_all_profiled_companies()
