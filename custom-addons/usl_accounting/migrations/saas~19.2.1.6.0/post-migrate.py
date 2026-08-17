from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    del version
    env = api.Environment(cr, SUPERUSER_ID, {})
    expense_groups = (
        env.ref("hr_expense.group_hr_expense_user")
        | env.ref("hr_expense.group_hr_expense_team_approver")
        | env.ref("hr_expense.group_hr_expense_manager")
    )
    candidates = env["res.users"].sudo().with_context(active_test=False).search([
        ("active", "=", True),
        ("share", "=", False),
        ("usl_expense_multi_company", "=", False),
    ])
    Employee = env["hr.employee"].sudo().with_context(active_test=False)
    for user in candidates:
        if len(user.company_ids) < 2 or not (user.all_group_ids & expense_groups):
            continue
        employee = Employee.search([
            "|",
            ("user_id", "=", user.id),
            ("work_contact_id", "=", user.partner_id.id),
        ], limit=1)
        if employee:
            user.usl_expense_multi_company = True
