from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .constants import (
    TESE_COMPONENT_BY_CODE,
    TESE_COMPONENT_CODES,
    TESE_COMPONENTS,
    TESE_INTERNAL_WRITE_TOKEN,
)


class UslTeseProfile(models.Model):
    _name = "usl.tese.profile"
    _description = "TESE Payroll Profile"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "employee_id, valid_from desc, id desc"
    _check_company_auto = True

    name = fields.Char(required=True, tracking=True)
    active = fields.Boolean(default=True, tracking=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        tracking=True,
        index=True,
    )
    currency_id = fields.Many2one(
        related="company_id.currency_id",
        readonly=True,
    )
    employee_id = fields.Many2one(
        "hr.employee",
        required=True,
        check_company=True,
        tracking=True,
        index=True,
    )
    hr_version_id = fields.Many2one(
        "hr.version",
        string="Preferred Employee Record",
        check_company=True,
        domain="[('employee_id', '=', employee_id), ('company_id', '=', company_id)]",
        tracking=True,
        help=(
            "Optional preferred employee record. Preparation still verifies that "
            "it is the one applicable to the payroll period."
        ),
    )
    employee_partner_id = fields.Many2one(
        related="employee_id.work_contact_id",
        string="Employee Contact",
        readonly=True,
        store=True,
    )
    collector_partner_id = fields.Many2one(
        "res.partner",
        string="TESE Collector",
        check_company=True,
        tracking=True,
        default=lambda self: self.env.company.tese_collector_partner_id,
    )
    valid_from = fields.Date(tracking=True)
    valid_to = fields.Date(tracking=True)
    default_hours = fields.Float(tracking=True)
    gross_salary = fields.Monetary(tracking=True)
    employee_contribution_total = fields.Monetary(tracking=True)
    employer_contribution_total = fields.Monetary(tracking=True)
    net_social = fields.Monetary(tracking=True)
    net_before_tax = fields.Monetary(tracking=True)
    income_tax_base = fields.Monetary(tracking=True)
    income_tax_rate = fields.Float(tracking=True)
    income_tax_amount = fields.Monetary(tracking=True)
    net_paid = fields.Monetary(tracking=True)
    component_line_ids = fields.One2many(
        "usl.tese.profile.line",
        "profile_id",
        string="Accounting Components",
        copy=True,
    )
    review_status = fields.Selection(
        [
            ("to_review", "To review"),
            ("ok", "Ready"),
            ("warning", "Warning"),
            ("archived", "Archived"),
        ],
        default="to_review",
        tracking=True,
        index=True,
    )
    review_message = fields.Text(tracking=True)
    last_used_date = fields.Date(readonly=True, copy=False)
    payslip_count = fields.Integer(compute="_compute_payslip_count")
    hr_wage_reference = fields.Monetary(
        related="hr_version_id.wage",
        readonly=True,
    )
    hr_hours_reference = fields.Float(compute="_compute_hr_hours_reference")
    hr_mismatch_warning = fields.Text(compute="_compute_hr_mismatch_warning")
    has_hr_mismatch = fields.Boolean(
        compute="_compute_has_hr_mismatch",
        store=True,
    )
    display_review_status = fields.Selection(
        selection=[
            ("to_review", "To review"),
            ("ok", "Ready"),
            ("warning", "Warning"),
            ("archived", "Archived"),
        ],
        string="Status",
        compute="_compute_display_review_status",
        store=True,
        index=True,
    )
    can_configure = fields.Boolean(compute="_compute_can_configure")

    _validity_order = models.Constraint(
        "CHECK(valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from)",
        "The profile end date cannot be earlier than its start date.",
    )
    _VERSIONED_FIELDS = {
        "company_id",
        "employee_id",
        "hr_version_id",
        "collector_partner_id",
        "valid_from",
        "default_hours",
        "gross_salary",
        "employee_contribution_total",
        "employer_contribution_total",
        "net_social",
        "net_before_tax",
        "income_tax_base",
        "income_tax_rate",
        "income_tax_amount",
        "net_paid",
        "component_line_ids",
    }

    @api.depends_context("uid")
    def _compute_can_configure(self):
        allowed = self.env.su or (
            self.env.user.has_group("hr.group_hr_manager")
            and self.env.user.has_group("account.group_account_manager")
        )
        for profile in self:
            profile.can_configure = allowed

    @api.depends("hr_version_id.hours_per_week")
    def _compute_hr_hours_reference(self):
        for profile in self:
            profile.hr_hours_reference = (
                profile.hr_version_id.hours_per_week * 52.0 / 12.0
            )

    @api.depends("employee_id")
    def _compute_payslip_count(self):
        grouped = self.env["usl.tese.payslip"].sudo()._read_group(
            [("profile_id", "in", self.ids)],
            ["profile_id"],
            ["__count"],
        )
        counts = {profile.id: count for profile, count in grouped}
        for profile in self:
            profile.payslip_count = counts.get(profile.id, 0)

    @api.depends(
        "hr_version_id.wage",
        "hr_version_id.hours_per_week",
        "gross_salary",
        "default_hours",
        "currency_id",
    )
    def _compute_hr_mismatch_warning(self):
        for profile in self:
            warnings = []
            version = profile.hr_version_id
            if version and not profile.currency_id.is_zero(
                profile.gross_salary - version.wage,
            ):
                warnings.append(_(
                    "Gross — TESE: %(provider).2f · HR: %(hr).2f.",
                    provider=profile.gross_salary,
                    hr=version.wage,
                ))
            if (
                version
                and profile.default_hours
                and profile.hr_hours_reference
                and abs(profile.default_hours - profile.hr_hours_reference) > 0.01
            ):
                warnings.append(_(
                    "Monthly hours — TESE: %(provider).2f · HR: %(hr).2f.",
                    provider=profile.default_hours,
                    hr=profile.hr_hours_reference,
                ))
            if warnings:
                warnings.append(_(
                    "Next: align TESE with HR, or keep the difference only if "
                    "it is intentional.",
                ))
            profile.hr_mismatch_warning = "\n".join(warnings)

    @api.depends(
        "hr_version_id.wage",
        "hr_version_id.hours_per_week",
        "gross_salary",
        "default_hours",
        "currency_id",
    )
    def _compute_has_hr_mismatch(self):
        for profile in self:
            version = profile.hr_version_id
            profile.has_hr_mismatch = bool(
                version
                and (
                    not profile.currency_id.is_zero(
                        profile.gross_salary - version.wage,
                    )
                    or (
                        profile.default_hours
                        and profile.hr_hours_reference
                        and abs(
                            profile.default_hours - profile.hr_hours_reference,
                        ) > 0.01
                    )
                ),
            )

    @api.depends("active", "review_status", "has_hr_mismatch")
    def _compute_display_review_status(self):
        for profile in self:
            if not profile.active:
                profile.display_review_status = "archived"
            elif profile.has_hr_mismatch:
                profile.display_review_status = "warning"
            else:
                profile.display_review_status = profile.review_status

    @api.constrains("employee_id", "hr_version_id")
    def _check_hr_version_employee(self):
        for profile in self:
            if (
                profile.hr_version_id
                and profile.hr_version_id.employee_id != profile.employee_id
            ):
                raise ValidationError(_(
                    "The preferred employee record must belong to the profile employee.",
                ))

    @api.constrains("employee_id", "company_id")
    def _check_employee_company(self):
        for profile in self:
            if profile.employee_id.company_id != profile.company_id:
                raise ValidationError(_(
                    "The profile and employee must belong to the same company.",
                ))

    @api.constrains("employee_id", "valid_from", "valid_to", "active")
    def _check_overlapping_profiles(self):
        for profile in self.filtered("active"):
            domain = [
                ("id", "!=", profile.id),
                ("active", "=", True),
                ("company_id", "=", profile.company_id.id),
                ("employee_id", "=", profile.employee_id.id),
                "|",
                ("valid_to", "=", False),
                ("valid_to", ">=", profile.valid_from or fields.Date.from_string("0001-01-01")),
                "|",
                ("valid_from", "=", False),
                ("valid_from", "<=", profile.valid_to or fields.Date.from_string("9999-12-31")),
            ]
            if self.search_count(domain):
                raise ValidationError(_(
                    "Active TESE profiles for one employee cannot have overlapping "
                    "validity periods.",
                ))

    def _check_configuration_access(self):
        if self.env.su:
            return
        if not (
            self.env.user.has_group("hr.group_hr_manager")
            and self.env.user.has_group("account.group_account_manager")
        ):
            raise AccessError(_(
                "TESE profile configuration requires both HR Administrator and "
                "Accounting Administrator access.",
            ))

    @api.model_create_multi
    def create(self, values_list):
        self._check_configuration_access()
        internal_write = (
            self.env.context.get("_tese_internal_write")
            is TESE_INTERNAL_WRITE_TOKEN
        )
        profile_ids = {
            values.get("profile_id") for values in values_list
            if values.get("profile_id")
        }
        if (
            not internal_write
            and self.env["usl.tese.profile"].browse(profile_ids).filtered(
                "payslip_count",
            )
        ):
            raise UserError(_(
                "Accounting details already used by payroll history cannot be "
                "extended. Create the next dated settings version instead.",
            ))
        return super().create(values_list)

    def write(self, values):
        self._check_configuration_access()
        internal_write = (
            self.env.context.get("_tese_internal_write")
            is TESE_INTERNAL_WRITE_TOKEN
        )
        if not internal_write and self._VERSIONED_FIELDS & set(values):
            used_profiles = self.filtered("payslip_count")
            if used_profiles:
                raise UserError(_(
                    "Settings already used by payroll history cannot be "
                    "edited. Create the next dated version instead.",
                ))
        return super().write(values)

    def unlink(self):
        self._check_configuration_access()
        if self.env["usl.tese.payslip"].search_count([
            ("profile_id", "in", self.ids),
        ]):
            raise UserError(_(
                "A TESE profile used by payroll history must be archived, not deleted.",
            ))
        return super().unlink()

    def action_load_french_defaults(self):
        self._check_configuration_access()
        if self.filtered("payslip_count"):
            raise UserError(_(
                "Settings already used by payroll history cannot be edited. "
                "Create the next dated version instead.",
            ))
        Account = self.env["account.account"]
        for profile in self:
            commands = [Command.clear()]
            missing = []
            ambiguous = []
            for component in TESE_COMPONENTS:
                accounts = Account.with_company(profile.company_id).search([
                    ("code", "=", component["code"]),
                    ("company_ids", "in", profile.company_id.id),
                ])
                if not accounts:
                    missing.append(component["code"])
                    continue
                if len(accounts) != 1:
                    ambiguous.append(component["code"])
                    continue
                commands.append(Command.create({
                    **component,
                    "account_id": accounts.id,
                }))
            if missing or ambiguous:
                details = []
                if missing:
                    details.append(_(
                        "missing accounts: %(codes)s",
                        codes=", ".join(missing),
                    ))
                if ambiguous:
                    details.append(_(
                        "ambiguous accounts: %(codes)s",
                        codes=", ".join(ambiguous),
                    ))
                raise UserError(_(
                    "French payroll defaults could not be loaded (%(details)s). "
                    "Install or correct the French chart of accounts; this action "
                    "never creates or rewrites accounts.",
                    details="; ".join(details),
                ))
            profile.component_line_ids = commands
            profile.review_status = "to_review"
            profile.review_message = _(
                "French defaults loaded. Enter the provider amounts and review "
                "the HR references before use.",
            )
        profile = self[-1]
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("TESE Payroll Settings"),
                "message": _(
                    "French payroll accounts loaded. Next: enter the TESE "
                    "amounts and review the employee contract references.",
                ),
                "type": "success",
                "sticky": False,
                "next": {
                    "type": "ir.actions.act_window",
                    "res_model": self._name,
                    "res_id": profile.id,
                    "view_mode": "form",
                    "views": [(False, "form")],
                    "target": "current",
                },
            },
        }

    def _validate_components(self):
        self.ensure_one()
        codes = self.component_line_ids.mapped("code")
        missing = sorted(set(TESE_COMPONENT_CODES) - set(codes))
        extra = sorted(set(codes) - set(TESE_COMPONENT_CODES))
        duplicated = sorted({
            code for code in codes if codes.count(code) > 1
        })
        if missing or extra or duplicated:
            raise ValidationError(_(
                "The accounting component set is invalid. Missing: %(missing)s; "
                "unexpected: %(extra)s; duplicated: %(duplicated)s.",
                missing=", ".join(missing) or _("none"),
                extra=", ".join(extra) or _("none"),
                duplicated=", ".join(duplicated) or _("none"),
            ))
        for line in self.component_line_ids:
            expected = TESE_COMPONENT_BY_CODE[line.code]
            if line.side != expected["side"] or line.role != expected["role"]:
                raise ValidationError(_(
                    "Component %(code)s must use side %(side)s and role %(role)s.",
                    code=line.code,
                    side=expected["side"],
                    role=expected["role"],
                ))
            account_companies = line.account_id.company_ids
            if line.account_id and self.company_id not in account_companies:
                raise ValidationError(_(
                    "Account %(account)s is not available to %(company)s.",
                    account=line.account_id.display_name,
                    company=self.company_id.display_name,
                ))
        return True

    def action_open_payslips(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("TESE Payroll Records"),
            "res_model": "usl.tese.payslip",
            "view_mode": "list,form",
            "domain": [("profile_id", "=", self.id)],
            "context": {
                "default_profile_id": self.id,
                "default_employee_id": self.employee_id.id,
                "default_company_id": self.company_id.id,
            },
        }

    def action_open_settings_revision(self):
        self.ensure_one()
        self._check_configuration_access()
        return {
            "type": "ir.actions.act_window",
            "name": _("Create payroll settings revision"),
            "res_model": "usl.tese.settings.revision.wizard",
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "new",
            "context": {
                "default_profile_id": self.id,
                "default_employee_id": self.employee_id.id,
                "default_effective_period": (
                    fields.Date.context_today(self).replace(day=1)
                ),
            },
        }


class UslTeseProfileLine(models.Model):
    _name = "usl.tese.profile.line"
    _description = "TESE Payroll Profile Component"
    _order = "sequence, id"
    _check_company_auto = True

    profile_id = fields.Many2one(
        "usl.tese.profile",
        required=True,
        ondelete="cascade",
        index=True,
    )
    company_id = fields.Many2one(
        related="profile_id.company_id",
        store=True,
        index=True,
    )
    currency_id = fields.Many2one(
        related="profile_id.currency_id",
        readonly=True,
    )
    sequence = fields.Integer(default=10)
    code = fields.Selection(
        [(code, component["name"]) for code, component in TESE_COMPONENT_BY_CODE.items()],
        required=True,
        index=True,
    )
    name = fields.Char(required=True, translate=True)
    side = fields.Selection(
        [("debit", "Debit"), ("credit", "Credit")],
        required=True,
    )
    role = fields.Selection(
        [
            ("gross", "Gross remuneration"),
            ("employer_contribution", "Employer contribution"),
            ("salary", "Salary payable"),
            ("social", "Social liability"),
            ("income_tax", "Withholding income tax"),
        ],
        required=True,
        index=True,
    )
    account_id = fields.Many2one(
        "account.account",
        required=True,
        check_company=True,
        domain="[('company_ids', 'in', company_id)]",
    )
    amount = fields.Monetary(required=True, default=0.0)

    _profile_code_unique = models.Constraint(
        "UNIQUE(profile_id, code)",
        "A component code can only appear once on a TESE profile.",
    )

    def _check_configuration_access(self):
        self.env["usl.tese.profile"]._check_configuration_access()

    @api.model_create_multi
    def create(self, values_list):
        self._check_configuration_access()
        return super().create(values_list)

    def write(self, values):
        self._check_configuration_access()
        internal_write = (
            self.env.context.get("_tese_internal_write")
            is TESE_INTERNAL_WRITE_TOKEN
        )
        if not internal_write and self.filtered(
            lambda line: line.profile_id.payslip_count,
        ):
            raise UserError(_(
                "Accounting details already used by payroll history cannot be "
                "edited. Create the next dated settings version instead.",
            ))
        return super().write(values)

    def unlink(self):
        self._check_configuration_access()
        internal_write = (
            self.env.context.get("_tese_internal_write")
            is TESE_INTERNAL_WRITE_TOKEN
        )
        if not internal_write and self.filtered(
            lambda line: line.profile_id.payslip_count,
        ):
            raise UserError(_(
                "Accounting details already used by payroll history cannot be "
                "deleted. Create the next dated settings version instead.",
            ))
        return super().unlink()
