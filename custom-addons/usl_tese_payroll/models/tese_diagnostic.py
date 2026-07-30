from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

from .constants import TESE_COMPONENT_CODES


class UslTeseDiagnosticIssue(models.Model):
    _name = "usl.tese.diagnostic.issue"
    _description = "TESE Payroll Diagnostic Issue"
    _order = "active desc, severity, last_seen_at desc, id desc"
    _check_company_auto = True

    name = fields.Char(required=True, readonly=True)
    stable_key = fields.Char(required=True, readonly=True, index=True)
    severity = fields.Selection(
        [
            ("blocking", "Blocking"),
            ("warning", "Warning"),
            ("info", "Information"),
        ],
        required=True,
        readonly=True,
        index=True,
    )
    category = fields.Char(required=True, readonly=True, index=True)
    object_model = fields.Char(readonly=True)
    object_id = fields.Integer(readonly=True)
    object_display_name = fields.Char(readonly=True)
    message = fields.Text(required=True, readonly=True)
    suggested_fix = fields.Text(readonly=True)
    active = fields.Boolean(default=True, readonly=True, index=True)
    resolved = fields.Boolean(readonly=True, index=True)
    first_seen_at = fields.Datetime(required=True, readonly=True)
    last_seen_at = fields.Datetime(required=True, readonly=True)
    resolved_at = fields.Datetime(readonly=True)
    run_label = fields.Char(readonly=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        readonly=True,
        index=True,
    )

    _company_stable_key_unique = models.Constraint(
        "UNIQUE(company_id, stable_key)",
        "A diagnostic key can only appear once per company.",
    )

    def _check_run_access(self):
        if self.env.su:
            return
        if not (
            self.env.user.has_group("hr.group_hr_manager")
            and (
                self.env.user.has_group("account.group_account_user")
                or self.env.user.has_group("account.group_account_manager")
            )
        ):
            raise AccessError(_(
                "Running TESE diagnostics requires both HR Administrator and "
                "Accountant access.",
            ))

    @api.model
    def _issue(self, issues, *, key, severity, category, record, message, fix):
        issues[key] = {
            "name": message.splitlines()[0][:120],
            "stable_key": key,
            "severity": severity,
            "category": category,
            "object_model": record._name if record else False,
            "object_id": record.id if record else 0,
            "object_display_name": record.display_name if record else False,
            "message": message,
            "suggested_fix": fix,
        }

    @api.model
    def _collect_company_issues(self, company):
        issues = {}
        if not company.tese_payroll_journal_id:
            self._issue(
                issues,
                key="company:journal",
                severity="blocking",
                category="configuration",
                record=company,
                message=_("The TESE payroll journal is not configured."),
                fix=_("Select a general journal in the TESE company configuration."),
            )
        elif (
            company.tese_payroll_journal_id.type != "general"
            or company.tese_payroll_journal_id.company_id != company
        ):
            self._issue(
                issues,
                key="company:journal-invalid",
                severity="blocking",
                category="configuration",
                record=company.tese_payroll_journal_id,
                message=_("The configured TESE journal is not a company general journal."),
                fix=_("Select a general journal belonging to this company."),
            )
        if not company.tese_collector_partner_id:
            self._issue(
                issues,
                key="company:collector",
                severity="warning",
                category="configuration",
                record=company,
                message=_("The default TESE collector is not configured."),
                fix=_("Select the URSSAF or provider partner on the company."),
            )

        profiles = self.env["usl.tese.profile"].search([
            ("company_id", "=", company.id),
            ("active", "=", True),
        ])
        for profile in profiles:
            try:
                profile._validate_components()
            except ValidationError as error:
                self._issue(
                    issues,
                    key=f"profile:{profile.id}:components",
                    severity="blocking",
                    category="profile",
                    record=profile,
                    message=str(error),
                    fix=_("Load and review the 11 French accounting components."),
                )
            codes = set(profile.component_line_ids.mapped("code"))
            if set(TESE_COMPONENT_CODES) - codes:
                continue
            non_reconcilable = profile.component_line_ids.filtered(
                lambda line: (
                    line.role in {"salary", "social", "income_tax"}
                    and not line.account_id.reconcile
                ),
            )
            if non_reconcilable:
                self._issue(
                    issues,
                    key=f"profile:{profile.id}:reconcile",
                    severity="blocking",
                    category="accounting",
                    record=profile,
                    message=_(
                        "Profile liability accounts do not all allow reconciliation: "
                        "%(accounts)s.",
                        accounts=", ".join(
                            non_reconcilable.mapped("account_id.code"),
                        ),
                    ),
                    fix=_(
                        "Have the Accounting Administrator review the chart "
                        "configuration; TESE Payroll never changes accounts silently.",
                    ),
                )
            if profile.hr_mismatch_warning:
                self._issue(
                    issues,
                    key=f"profile:{profile.id}:hr-reference",
                    severity="warning",
                    category="hr",
                    record=profile,
                    message=profile.hr_mismatch_warning,
                    fix=_("Confirm that the provider figures intentionally differ from HR."),
                )

        payslips = self.env["usl.tese.payslip"].search([
            ("company_id", "=", company.id),
            ("state", "!=", "cancelled"),
        ])
        for payslip in payslips:
            if payslip.state == "to_post" and not payslip.attachment_id:
                self._issue(
                    issues,
                    key=f"payslip:{payslip.id}:pdf",
                    severity="blocking",
                    category="document",
                    record=payslip,
                    message=_("The payroll is ready to post but has no provider PDF."),
                    fix=_("Attach the original TESE payroll PDF before posting."),
                )
            if payslip.move_id and payslip.move_id.tese_payslip_id != payslip:
                self._issue(
                    issues,
                    key=f"payslip:{payslip.id}:backlink",
                    severity="blocking",
                    category="accounting",
                    record=payslip,
                    message=_("The linked journal entry has no matching TESE backlink."),
                    fix=_("Correct the link before continuing the workflow."),
                )
            if (
                payslip.state in {"to_reconcile", "paid"}
                and (not payslip.move_id or payslip.move_id.state != "posted")
            ):
                self._issue(
                    issues,
                    key=f"payslip:{payslip.id}:posted",
                    severity="blocking",
                    category="accounting",
                    record=payslip,
                    message=_("The payroll status expects a posted journal entry."),
                    fix=_("Review the linked entry and payroll state."),
                )
            if payslip.state == "paid" and payslip.move_id.state == "posted":
                salary_ok, tese_ok, salary_open, tese_open = (
                    payslip._residual_status()
                )
                if not salary_ok or not tese_ok:
                    self._issue(
                        issues,
                        key=f"payslip:{payslip.id}:residual",
                        severity="blocking",
                        category="settlement",
                        record=payslip,
                        message=_(
                            "Paid payroll has reopened liabilities: salary "
                            "%(salary).2f; TESE %(tese).2f.",
                            salary=salary_open,
                            tese=tese_open,
                        ),
                        fix=_("Refresh payments and return the payroll to reconciliation."),
                    )
        return issues

    @api.model
    def action_run_diagnostics(self):
        self._check_run_access()
        company = self.env.company
        issues = self._collect_company_issues(company)
        now = fields.Datetime.now()
        run_label = fields.Datetime.to_string(now)
        existing = self.with_context(active_test=False).sudo().search([
            ("company_id", "=", company.id),
        ])
        existing_by_key = {issue.stable_key: issue for issue in existing}
        for key, values in issues.items():
            values.update({
                "company_id": company.id,
                "active": True,
                "resolved": False,
                "last_seen_at": now,
                "resolved_at": False,
                "run_label": run_label,
            })
            issue = existing_by_key.get(key)
            if issue:
                issue.write(values)
            else:
                self.sudo().create({
                    **values,
                    "first_seen_at": now,
                })
        resolved = existing.filtered(
            lambda issue: issue.active and issue.stable_key not in issues,
        )
        resolved.write({
            "active": False,
            "resolved": True,
            "resolved_at": now,
            "run_label": run_label,
        })
        return {
            "type": "ir.actions.act_window",
            "name": _("TESE Diagnostics"),
            "res_model": self._name,
            "view_mode": "list,form",
            "domain": [("company_id", "=", company.id)],
            "context": {"search_default_active_issues": 1},
        }

    def action_open_object(self):
        self.ensure_one()
        allowed_models = {
            "res.company",
            "account.journal",
            "usl.tese.profile",
            "usl.tese.payslip",
        }
        if self.object_model not in allowed_models or not self.object_id:
            return False
        record = self.env[self.object_model].browse(self.object_id).exists()
        if not record:
            return False
        return {
            "type": "ir.actions.act_window",
            "name": self.object_display_name,
            "res_model": self.object_model,
            "res_id": self.object_id,
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "current",
        }
