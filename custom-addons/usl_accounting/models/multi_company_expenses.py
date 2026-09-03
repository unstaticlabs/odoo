from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError


class ResUsers(models.Model):
    _inherit = "res.users"

    usl_expense_multi_company = fields.Boolean(
        string="Expenses in all allowed companies",
        groups="base.group_system",
        help=(
            "Maintain one company-specific employee profile in each allowed "
            "company. This lets the user submit expenses after switching the "
            "active company without merging HR, payroll, approval or "
            "accounting records between legal entities."
        ),
    )
    usl_expense_excluded_company_ids = fields.Many2many(
        "res.company",
        "res_users_expense_company_exclusion_rel",
        "user_id",
        "company_id",
        string="Companies without an employee profile",
        groups="base.group_system",
        help=(
            "Keep access to these companies without creating an employee "
            "profile for expense submission. Existing employee records are "
            "never archived automatically."
        ),
    )
    usl_expense_company_profile_status = fields.Selection(
        [
            ("disabled", "Not enabled"),
            ("ready", "Ready"),
            ("attention", "Needs attention"),
        ],
        compute="_compute_usl_expense_company_profile_status",
        string="Multi-company expense access",
        groups="base.group_system",
    )
    usl_expense_company_profile_message = fields.Char(
        compute="_compute_usl_expense_company_profile_status",
        groups="base.group_system",
    )

    @api.depends(
        "active",
        "share",
        "company_ids",
        "usl_expense_excluded_company_ids",
        "all_employee_ids.active",
        "all_employee_ids.company_id",
        "all_employee_ids.user_id",
        "usl_expense_multi_company",
    )
    def _compute_usl_expense_company_profile_status(self):
        Employee = self.env["hr.employee"].sudo().with_context(
            active_test=False,
        )
        for user in self:
            if not user.usl_expense_multi_company:
                user.usl_expense_company_profile_status = "disabled"
                user.usl_expense_company_profile_message = _(
                    "Enable this option to submit expenses in every allowed "
                    "company.",
                )
                continue
            expense_companies = user.company_ids - user.usl_expense_excluded_company_ids
            profiles = Employee.search([
                ("user_id", "=", user.id),
                ("company_id", "in", expense_companies.ids),
            ])
            ready_company_ids = set(
                profiles.filtered("active").company_id.ids,
            )
            missing = expense_companies.filtered(
                lambda company: company.id not in ready_company_ids,
            )
            if user.share or not user.active or missing:
                user.usl_expense_company_profile_status = "attention"
                user.usl_expense_company_profile_message = (
                    _(
                        "Missing an active employee profile for: %(companies)s.",
                        companies=", ".join(missing.mapped("display_name")),
                    )
                    if missing
                    else _(
                        "Only active internal users can submit expenses.",
                    )
                )
            else:
                user.usl_expense_company_profile_status = "ready"
                user.usl_expense_company_profile_message = _(
                    "Expense submission is ready in %(count)s companies.",
                    count=len(expense_companies),
                )

    def _usl_expense_profile_candidates(self, company):
        self.ensure_one()
        return self.env["hr.employee"].sudo().with_context(
            active_test=False,
        ).search([
            ("company_id", "=", company.id),
            ("user_id", "=", False),
            ("work_contact_id", "=", self.partner_id.id),
        ])

    def _usl_ensure_expense_company_profiles(self, *, strict=False):
        """Create the native per-company employee records expenses require.

        The records remain independent legal/HR objects.  Only the common Odoo
        user and work contact are shared; company-specific departments,
        contracts, approvers, payroll and private fields are never copied.
        """
        Employee = self.env["hr.employee"].sudo().with_context(
            active_test=False,
            tracking_disable=True,
            mail_create_nolog=True,
            mail_create_nosubscribe=True,
            usl_expense_profile_sync=True,
        )
        problems = []
        for user in self.sudo():
            if not user.usl_expense_multi_company:
                continue
            if user.share or not user.active:
                problems.append(
                    _("%(user)s is not an active internal user.", user=user.name),
                )
                continue
            expense_companies = user.company_ids - user.usl_expense_excluded_company_ids
            for company in expense_companies:
                existing = Employee.search([
                    ("user_id", "=", user.id),
                    ("company_id", "=", company.id),
                ], limit=1)
                if existing:
                    if not existing.active:
                        problems.append(
                            _(
                                "%(user)s has an archived employee profile in "
                                "%(company)s.",
                                user=user.name,
                                company=company.display_name,
                            ),
                        )
                    continue
                candidates = user._usl_expense_profile_candidates(company)
                if len(candidates) > 1:
                    problems.append(
                        _(
                            "%(user)s has several unlinked employee profiles "
                            "in %(company)s.",
                            user=user.name,
                            company=company.display_name,
                        ),
                    )
                    continue
                if candidates:
                    candidates.write({"user_id": user.id})
                    continue
                reference_profile = Employee.search([
                    "|",
                    ("user_id", "=", user.id),
                    ("work_contact_id", "=", user.partner_id.id),
                ], order="active desc, id", limit=1)
                Employee.create({
                    "name": reference_profile.name or user.name,
                    "company_id": company.id,
                    "user_id": user.id,
                    "work_contact_id": user.partner_id.id,
                    "work_email": user.email,
                })
        if strict and problems:
            raise UserError("\n".join(problems))
        return problems

    def action_usl_sync_expense_company_profiles(self):
        if not self.env.user.has_group("base.group_system"):
            raise AccessError(
                _("Only an administrator can configure employee profiles."),
            )
        self._usl_ensure_expense_company_profiles(strict=True)
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Multi-company expenses are ready"),
                "message": _(
                    "Each selected company has its own employee profile. "
                    "Switch the active company before creating an expense.",
                ),
                "type": "success",
                "sticky": False,
            },
        }

    @api.model_create_multi
    def create(self, vals_list):
        users = super().create(vals_list)
        users.filtered("usl_expense_multi_company")._usl_ensure_expense_company_profiles()
        return users

    def write(self, vals):
        result = super().write(vals)
        if {
            "company_ids",
            "active",
            "share",
            "usl_expense_multi_company",
            "usl_expense_excluded_company_ids",
        } & vals.keys():
            self.filtered("usl_expense_multi_company")._usl_ensure_expense_company_profiles()
        return result
