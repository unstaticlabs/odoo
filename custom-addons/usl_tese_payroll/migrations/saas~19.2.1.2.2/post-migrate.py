from dateutil.relativedelta import relativedelta

from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    Profile = env["usl.tese.profile"].with_context(active_test=False)
    open_archived_profiles = Profile.search([
        ("active", "=", False),
        ("valid_from", "!=", False),
        ("valid_to", "=", False),
    ])
    for profile in open_archived_profiles:
        next_profile = Profile.search([
            ("id", "!=", profile.id),
            ("company_id", "=", profile.company_id.id),
            ("employee_id", "=", profile.employee_id.id),
            "|",
            ("valid_from", ">", profile.valid_from),
            "&",
            ("valid_from", "=", profile.valid_from),
            ("id", ">", profile.id),
        ], order="valid_from, id", limit=1)
        if not next_profile:
            continue
        archive_end = next_profile.valid_from - relativedelta(days=1)
        profile.valid_to = max(profile.valid_from, archive_end)
