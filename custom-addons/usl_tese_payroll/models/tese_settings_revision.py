from dateutil.relativedelta import relativedelta

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools import format_date

from .constants import TESE_COMPONENT_BY_CODE

PROFILE_NUMBER_FIELDS = (
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
)


class UslTeseSettingsRevisionWizard(models.TransientModel):
    _name = "usl.tese.settings.revision.wizard"
    _description = "TESE Payroll Settings and Contract Revision"

    payslip_id = fields.Many2one(
        "usl.tese.payslip",
        readonly=True,
        ondelete="cascade",
    )
    profile_id = fields.Many2one(
        "usl.tese.profile",
        string="Current TESE Settings",
        required=True,
        readonly=True,
    )
    employee_id = fields.Many2one(
        "hr.employee",
        required=True,
        readonly=True,
    )
    company_id = fields.Many2one(
        "res.company",
        required=True,
        readonly=True,
    )
    currency_id = fields.Many2one(
        related="company_id.currency_id",
        readonly=True,
    )
    effective_period = fields.Date(
        string="Effective Month",
        required=True,
    )
    update_tese = fields.Boolean(
        string="TESE figures changed",
        default=True,
    )
    update_contract = fields.Boolean(
        string="Employment terms changed",
    )

    default_hours = fields.Float(string="Declared Monthly Hours")
    gross_salary = fields.Monetary()
    employee_contribution_total = fields.Monetary()
    employer_contribution_total = fields.Monetary()
    net_social = fields.Monetary()
    net_before_tax = fields.Monetary()
    income_tax_base = fields.Monetary()
    income_tax_rate = fields.Float()
    income_tax_amount = fields.Monetary()
    net_paid = fields.Monetary()
    component_line_ids = fields.One2many(
        "usl.tese.settings.revision.line",
        "wizard_id",
        string="Accounting Detail",
    )

    source_version_id = fields.Many2one(
        "hr.version",
        string="Current Contract Version",
        readonly=True,
    )
    wage = fields.Monetary(string="Monthly Wage")
    hours_per_week = fields.Float()
    resource_calendar_id = fields.Many2one(
        "resource.calendar",
        string="Working Hours",
        domain="[('company_id', 'in', (False, company_id))]",
    )
    employee_type_id = fields.Many2one(
        "hr.employee.type",
        string="Employee Type",
    )
    contract_date_start = fields.Date(string="Contract Start")
    contract_date_end = fields.Date(string="Contract End")
    comparison_warning = fields.Text(
        string="TESE / HR Difference",
        compute="_compute_comparison_warning",
    )

    @api.depends(
        "gross_salary",
        "default_hours",
        "update_contract",
        "wage",
        "hours_per_week",
        "source_version_id.wage",
        "source_version_id.hours_per_week",
        "currency_id",
    )
    def _compute_comparison_warning(self):
        for wizard in self:
            version = wizard.source_version_id
            hr_wage = wizard.wage if wizard.update_contract else version.wage
            weekly_hours = (
                wizard.hours_per_week
                if wizard.update_contract
                else version.hours_per_week
            )
            hr_monthly_hours = weekly_hours * 52.0 / 12.0
            warnings = []
            if wizard.currency_id and not wizard.currency_id.is_zero(
                wizard.gross_salary - hr_wage,
            ):
                warnings.append(_(
                    "Gross — TESE: %(provider).2f · HR: %(hr).2f.",
                    provider=wizard.gross_salary,
                    hr=hr_wage,
                ))
            if (
                wizard.default_hours
                and hr_monthly_hours
                and abs(wizard.default_hours - hr_monthly_hours) > 0.01
            ):
                warnings.append(_(
                    "Monthly hours — TESE: %(provider).2f · HR: %(hr).2f.",
                    provider=wizard.default_hours,
                    hr=hr_monthly_hours,
                ))
            if warnings:
                warnings.append(_(
                    "Next: align TESE with HR, or keep the difference only if "
                    "it is intentional.",
                ))
            wizard.comparison_warning = "\n".join(warnings)

    @api.model
    def _month_start(self, value):
        value = fields.Date.to_date(value)
        return value.replace(day=1) if value else False

    @api.model
    def default_get(self, field_names):
        values = super().default_get(field_names)
        payslip = self.env["usl.tese.payslip"].browse(
            values.get("payslip_id")
            or self.env.context.get("default_payslip_id"),
        ).exists()
        profile = self.env["usl.tese.profile"].with_context(
            active_test=False,
        ).browse(
            values.get("profile_id")
            or self.env.context.get("default_profile_id")
            or payslip.profile_id.id,
        ).exists()
        employee = self.env["hr.employee"].browse(
            values.get("employee_id")
            or self.env.context.get("default_employee_id")
            or profile.employee_id.id
            or payslip.employee_id.id,
        ).exists()
        if not profile or not employee:
            raise UserError(_(
                "Choose an employee and current TESE settings before creating "
                "a revision. Next: open the payroll or profile that you want "
                "to revise and start again.",
            ))
        effective_period = self._month_start(
            values.get("effective_period")
            or self.env.context.get("default_effective_period")
            or payslip.pay_period
            or fields.Date.context_today(self),
        )
        versions = self.env["usl.tese.payslip"]._applicable_hr_versions(
            employee,
            effective_period,
        )
        version = (
            profile.hr_version_id
            if profile.hr_version_id in versions
            else versions if len(versions) == 1
            else employee._get_version(effective_period)
        )
        values.update({
            "payslip_id": payslip.id,
            "profile_id": profile.id,
            "employee_id": employee.id,
            "company_id": profile.company_id.id,
            "effective_period": effective_period,
            "source_version_id": version.id,
            "component_line_ids": [
                Command.create({
                    "sequence": line.sequence,
                    "code": line.code,
                    "name": line.name,
                    "account_id": line.account_id.id,
                    "amount": line.amount,
                })
                for line in profile.component_line_ids.sorted("sequence")
            ],
        })
        for field_name in PROFILE_NUMBER_FIELDS:
            values[field_name] = profile[field_name]
        if version:
            values.update({
                "wage": version.wage,
                "hours_per_week": version.hours_per_week,
                "resource_calendar_id": version.resource_calendar_id.id,
                "employee_type_id": version.employee_type_id.id,
                "contract_date_start": version.contract_date_start,
                "contract_date_end": version.contract_date_end,
            })
        return values

    def _create_contract_version(self):
        self.ensure_one()
        effective_date = self.effective_period
        employee = self.employee_id
        had_contract = employee._is_in_contract(effective_date)
        if had_contract:
            version = employee.create_version({
                "date_version": effective_date,
            })
        else:
            version = employee.create_contract(effective_date)
        version.write({
            "wage": self.wage,
            "hours_per_week": self.hours_per_week,
            "resource_calendar_id": self.resource_calendar_id.id,
            "employee_type_id": self.employee_type_id.id,
            "contract_date_start": (
                self.contract_date_start
                if had_contract
                else effective_date
            ),
            "contract_date_end": self.contract_date_end,
        })
        return version

    def _create_profile_revision(self, version):
        self.ensure_one()
        old_profile = self.profile_id
        effective_date = self.effective_period
        next_profile = self.env["usl.tese.profile"].with_context(
            active_test=False,
        ).search([
            ("id", "!=", old_profile.id),
            ("company_id", "=", self.company_id.id),
            ("employee_id", "=", self.employee_id.id),
            ("valid_from", ">", effective_date),
        ], order="valid_from, id", limit=1)
        new_valid_to = (
            next_profile.valid_from - relativedelta(days=1)
            if next_profile
            else False
        )
        archive_values = {
            "active": False,
            "review_status": "archived",
        }
        archive_end = effective_date - relativedelta(days=1)
        if old_profile.valid_from and archive_end < old_profile.valid_from:
            # Two revisions can be created for the same payroll month while a
            # draft is being corrected. Keep the archived version as a dated
            # one-day historical record instead of leaving it open-ended.
            archive_end = old_profile.valid_from
        if not old_profile.valid_to or old_profile.valid_to > archive_end:
            archive_values["valid_to"] = archive_end
        old_profile.write(archive_values)

        period_label = format_date(
            self.env,
            effective_date,
            date_format="MMMM y",
        )
        new_active = not next_profile
        profile_values = {
            "name": _(
                "%(employee)s — TESE %(period)s",
                employee=self.employee_id.name,
                period=period_label,
            ),
            "active": new_active,
            "employee_id": self.employee_id.id,
            "hr_version_id": version.id,
            "valid_from": effective_date,
            "valid_to": new_valid_to,
            "last_used_date": False,
            "review_status": "ok" if new_active else "archived",
            "review_message": _(
                "Created from %(profile)s for %(period)s.",
                profile=old_profile.display_name,
                period=period_label,
            ),
        }
        for field_name in PROFILE_NUMBER_FIELDS:
            profile_values[field_name] = self[field_name]
        new_profile = old_profile.copy(default=profile_values)
        lines_by_code = {
            line.code: line for line in new_profile.component_line_ids
        }
        for wizard_line in self.component_line_ids:
            lines_by_code[wizard_line.code].amount = wizard_line.amount
        new_profile._validate_components()
        old_profile.message_post(body=_(
            "Superseded by %(profile)s from %(date)s.",
            profile=new_profile.display_name,
            date=effective_date,
        ))
        new_profile.message_post(body=_(
            "Created from %(profile)s with contract version %(version)s.",
            profile=old_profile.display_name,
            version=version.display_name,
        ))
        return new_profile

    def action_apply(self):
        self.ensure_one()
        self.profile_id._check_configuration_access()
        self.effective_period = self._month_start(self.effective_period)
        if not self.update_tese and not self.update_contract:
            raise ValidationError(_(
                "Select TESE figures, employment terms, or both. Next: tick at "
                "least one change type above.",
            ))
        if self.payslip_id and self.payslip_id.pay_period != self.effective_period:
            raise ValidationError(_(
                "The revision month must match the payroll month. Next: use the "
                "payroll month shown on this record.",
            ))
        if len(self.component_line_ids) != len(TESE_COMPONENT_BY_CODE):
            raise ValidationError(_(
                "The TESE accounting detail must contain exactly eleven lines. "
                "Next: cancel and reopen the wizard from a complete profile.",
            ))
        version = (
            self._create_contract_version()
            if self.update_contract
            else self.source_version_id
        )
        if not version:
            raise ValidationError(_(
                "No contract version covers the selected payroll month. Next: "
                "enable Employment terms changed to create one.",
            ))
        new_profile = self._create_profile_revision(version)
        if self.payslip_id:
            self.payslip_id._apply_revised_settings(new_profile, version)
            next_action = {
                "type": "ir.actions.act_window",
                "name": _("TESE Payroll"),
                "res_model": "usl.tese.payslip",
                "res_id": self.payslip_id.id,
                "view_mode": "form",
                "views": [(False, "form")],
                "target": "current",
            }
            message = _(
                "New recurring settings applied. The previous version was "
                "archived, not deleted. Next: review this payroll and its draft "
                "journal entry before posting.",
            )
        else:
            next_action = {
                "type": "ir.actions.act_window",
                "name": _("TESE Payroll Settings"),
                "res_model": "usl.tese.profile",
                "res_id": new_profile.id,
                "view_mode": "form",
                "views": [(False, "form")],
                "target": "current",
            }
            message = _(
                "New recurring settings created. The previous version was "
                "archived, not deleted. Next: review the new version before "
                "using it for payroll.",
            )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("TESE Payroll Settings"),
                "message": message,
                "type": "success",
                "sticky": False,
                "next": next_action,
            },
        }


class UslTeseSettingsRevisionLine(models.TransientModel):
    _name = "usl.tese.settings.revision.line"
    _description = "TESE Payroll Settings Revision Accounting Line"
    _order = "sequence, id"

    wizard_id = fields.Many2one(
        "usl.tese.settings.revision.wizard",
        required=True,
        ondelete="cascade",
    )
    company_id = fields.Many2one(
        related="wizard_id.company_id",
        readonly=True,
    )
    currency_id = fields.Many2one(
        related="wizard_id.currency_id",
        readonly=True,
    )
    sequence = fields.Integer(readonly=True)
    code = fields.Selection(
        [
            (code, component["name"])
            for code, component in TESE_COMPONENT_BY_CODE.items()
        ],
        required=True,
        readonly=True,
    )
    name = fields.Char(readonly=True)
    account_id = fields.Many2one(
        "account.account",
        readonly=True,
    )
    amount = fields.Monetary(required=True)
