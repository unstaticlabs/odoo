import json


users = env["res.users"].sudo().with_context(active_test=False).search([
    ("usl_expense_multi_company", "=", True),
    ("active", "=", True),
    ("share", "=", False),
])
users._usl_ensure_expense_company_profiles(strict=True)

profiles = env["hr.employee"].sudo().with_context(active_test=False).search([
    ("user_id", "in", users.ids),
    ("active", "=", True),
])
summary = {
    user.login: sorted(
        profiles.filtered(lambda profile: profile.user_id == user)
        .company_id.mapped("name"),
    )
    for user in users
}
missing = {
    user.login: sorted(set(user.company_ids.mapped("name")) - set(summary[user.login]))
    for user in users
}
missing = {login: companies for login, companies in missing.items() if companies}
if missing:
    raise RuntimeError(f"Missing multi-company expense profiles: {missing}")

env.cr.commit()
print(json.dumps({"multi_company_expense_profiles": summary}, indent=2, sort_keys=True))
