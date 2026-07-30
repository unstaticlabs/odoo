import calendar
import hashlib
import unicodedata
from datetime import date, timedelta

from dateutil.relativedelta import relativedelta

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import format_date

from .constants import (
    TESE_COMPONENT_CODES,
    TESE_INTERNAL_WRITE_TOKEN,
    TESE_LIABILITY_ROLES,
)


def _normalized(value):
    value = unicodedata.normalize("NFKD", value or "")
    return "".join(
        character for character in value if not unicodedata.combining(character)
    ).lower()


class UslTesePayslip(models.Model):
    _name = "usl.tese.payslip"
    _description = "TESE Payroll Record"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "period_end desc, employee_id, id desc"
    _check_company_auto = True

    name = fields.Char(
        required=True,
        default=lambda self: _("New TESE payroll"),
        tracking=True,
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("prepared", "Prepared"),
            ("to_post", "Ready to post"),
            ("to_reconcile", "To reconcile"),
            ("paid", "Paid"),
            ("cancelled", "Cancelled"),
        ],
        required=True,
        default="draft",
        tracking=True,
        index=True,
        copy=False,
    )
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
    profile_id = fields.Many2one(
        "usl.tese.profile",
        check_company=True,
        tracking=True,
        index=True,
        domain="[('company_id', '=', company_id), ('active', '=', True)]",
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
        string="Employee Record",
        check_company=True,
        readonly=True,
        copy=False,
        index=True,
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
        copy=False,
    )
    pay_month = fields.Integer(required=True, tracking=True)
    pay_year = fields.Integer(required=True, tracking=True)
    period_start = fields.Date(readonly=True, copy=False, index=True)
    period_end = fields.Date(readonly=True, copy=False, index=True)
    payment_date = fields.Date(
        string="Salary Payment Date",
        tracking=True,
        copy=False,
    )
    payslip_date = fields.Date(tracking=True, copy=False)
    tese_payment_date = fields.Date(
        string="TESE Collection Date",
        tracking=True,
        copy=False,
    )
    tese_reference = fields.Char(
        string="TESE Reference",
        required=True,
        tracking=True,
        index=True,
    )
    hours = fields.Float(tracking=True, copy=False)
    attachment_id = fields.Many2one(
        "ir.attachment",
        string="Payroll PDF",
        ondelete="restrict",
        copy=False,
        tracking=True,
    )
    document_note = fields.Text(copy=False)
    document_status = fields.Selection(
        [
            ("missing", "Document missing"),
            ("linked", "Document linked"),
            ("warning", "Link to review"),
            ("ok", "Ready"),
        ],
        compute="_compute_document_status",
    )
    document_message = fields.Char(compute="_compute_document_status")

    gross_salary = fields.Monetary(tracking=True, copy=False)
    employee_contribution_total = fields.Monetary(tracking=True, copy=False)
    employer_contribution_total = fields.Monetary(tracking=True, copy=False)
    net_social = fields.Monetary(tracking=True, copy=False)
    net_before_tax = fields.Monetary(tracking=True, copy=False)
    income_tax_base = fields.Monetary(tracking=True, copy=False)
    income_tax_rate = fields.Float(tracking=True, copy=False)
    income_tax_amount = fields.Monetary(tracking=True, copy=False)
    net_paid = fields.Monetary(tracking=True, copy=False)
    component_line_ids = fields.One2many(
        "usl.tese.payslip.line",
        "payslip_id",
        string="Accounting Snapshot",
        copy=False,
    )

    move_id = fields.Many2one(
        "account.move",
        string="Payroll Journal Entry",
        check_company=True,
        readonly=True,
        copy=False,
        ondelete="restrict",
        index=True,
    )
    salary_settlement_move_id = fields.Many2one(
        "account.move",
        string="Salary Settlement Entry",
        check_company=True,
        readonly=True,
        copy=False,
        ondelete="restrict",
    )
    tese_settlement_move_id = fields.Many2one(
        "account.move",
        string="TESE Settlement Entry",
        check_company=True,
        readonly=True,
        copy=False,
        ondelete="restrict",
    )
    move_ref = fields.Char(readonly=True, copy=False)
    total_debit = fields.Monetary(readonly=True, copy=False)
    total_credit = fields.Monetary(readonly=True, copy=False)
    balance_difference = fields.Monetary(readonly=True, copy=False)
    preparation_ok = fields.Boolean(readonly=True, copy=False)
    preparation_message = fields.Text(readonly=True, copy=False)
    control_checklist = fields.Text(readonly=True, copy=False)
    preparation_warnings = fields.Text(readonly=True, copy=False)
    payment_check_ok = fields.Boolean(readonly=True, copy=False)
    payment_check_message = fields.Text(readonly=True, copy=False)
    bank_reconcile_message = fields.Text(readonly=True, copy=False)

    tese_contribution_total = fields.Monetary(readonly=True, copy=False)
    tese_income_tax_total = fields.Monetary(readonly=True, copy=False)
    tese_detailed_total = fields.Monetary(readonly=True, copy=False)
    tese_bank_amount = fields.Monetary(
        string="TESE Bank Amount",
        tracking=True,
        copy=False,
    )
    tese_bank_difference = fields.Monetary(readonly=True, copy=False)

    salary_payment_best_line_id = fields.Many2one(
        "account.move.line",
        string="Best Salary Payment",
        readonly=True,
        copy=False,
        check_company=True,
    )
    salary_payment_candidate_count = fields.Integer(readonly=True, copy=False)
    salary_payment_match_score = fields.Float(readonly=True, copy=False)
    salary_payment_match_message = fields.Text(readonly=True, copy=False)
    salary_payment_reconciled = fields.Boolean(readonly=True, copy=False)
    salary_payment_candidate_date = fields.Date(readonly=True, copy=False)
    salary_payment_candidate_amount = fields.Monetary(readonly=True, copy=False)
    salary_payment_candidate_label = fields.Char(readonly=True, copy=False)
    salary_payment_candidate_difference = fields.Monetary(
        readonly=True,
        copy=False,
    )

    tese_payment_best_line_id = fields.Many2one(
        "account.move.line",
        string="Best TESE Collection",
        readonly=True,
        copy=False,
        check_company=True,
    )
    tese_payment_candidate_count = fields.Integer(readonly=True, copy=False)
    tese_payment_match_score = fields.Float(readonly=True, copy=False)
    tese_payment_match_message = fields.Text(readonly=True, copy=False)
    tese_payment_reconciled = fields.Boolean(readonly=True, copy=False)
    tese_payment_candidate_date = fields.Date(readonly=True, copy=False)
    tese_payment_candidate_amount = fields.Monetary(readonly=True, copy=False)
    tese_payment_candidate_label = fields.Char(readonly=True, copy=False)
    tese_payment_candidate_difference = fields.Monetary(
        readonly=True,
        copy=False,
    )

    profile_snapshot_label = fields.Char(readonly=True, copy=False)
    profile_snapshot_text = fields.Text(readonly=True, copy=False)
    employee_snapshot_name = fields.Char(readonly=True, copy=False)
    employee_partner_snapshot_id = fields.Many2one(
        "res.partner",
        readonly=True,
        copy=False,
        ondelete="restrict",
    )
    hr_wage_snapshot = fields.Monetary(readonly=True, copy=False)
    hr_hours_snapshot = fields.Float(readonly=True, copy=False)
    profile_valid_from_snapshot = fields.Date(readonly=True, copy=False)
    profile_valid_to_snapshot = fields.Date(readonly=True, copy=False)
    can_workflow = fields.Boolean(compute="_compute_can_workflow")

    _period_employee_unique = models.Constraint(
        "UNIQUE(company_id, employee_id, pay_year, pay_month)",
        "An employee can only have one TESE payroll record for a month.",
    )
    _reference_unique = models.Constraint(
        "UNIQUE(company_id, tese_reference)",
        "A TESE reference can only be used once per company.",
    )
    _month_range = models.Constraint(
        "CHECK(pay_month >= 1 AND pay_month <= 12)",
        "The payroll month must be between 1 and 12.",
    )
    _year_range = models.Constraint(
        "CHECK(pay_year >= 2020 AND pay_year <= 2100)",
        "The payroll year must be between 2020 and 2100.",
    )

    _INPUT_FIELDS = {
        "company_id",
        "profile_id",
        "employee_id",
        "pay_month",
        "pay_year",
        "payment_date",
        "payslip_date",
        "tese_payment_date",
        "tese_reference",
        "attachment_id",
        "document_note",
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
        "collector_partner_id",
        "hours",
    }

    @api.depends_context("uid")
    def _compute_can_workflow(self):
        allowed = self.env.su or (
            self.env.user.has_group("hr.group_hr_manager")
            and (
                self.env.user.has_group("account.group_account_user")
                or self.env.user.has_group("account.group_account_manager")
            )
        )
        for payslip in self:
            payslip.can_workflow = allowed

    @api.depends("attachment_id", "attachment_id.mimetype")
    def _compute_document_status(self):
        for payslip in self:
            if not payslip.attachment_id:
                payslip.document_status = "missing"
                payslip.document_message = _("Attach the provider payroll PDF.")
            elif payslip.attachment_id.mimetype != "application/pdf":
                payslip.document_status = "warning"
                payslip.document_message = _("The linked attachment is not a PDF.")
            elif (
                payslip.id
                and (
                    payslip.attachment_id.res_model != payslip._name
                    or payslip.attachment_id.res_id != payslip.id
                )
            ):
                payslip.document_status = "linked"
                payslip.document_message = _(
                    "The PDF is linked but is not yet the payroll record's "
                    "native attachment.",
                )
            else:
                payslip.document_status = "ok"
                payslip.document_message = _("Provider payroll PDF ready.")

    def _check_read_access_role(self):
        if self.env.su:
            return
        if not (
            self.env.user.has_group("hr.group_hr_manager")
            and (
                self.env.user.has_group("account.group_account_readonly")
                or self.env.user.has_group("account.group_account_manager")
            )
        ):
            raise AccessError(_(
                "TESE payroll requires both HR Administrator and Accounting "
                "read access.",
            ))

    def _check_workflow_access(self):
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
                "Changing TESE payroll requires both HR Administrator and "
                "Accountant access.",
            ))

    @api.model_create_multi
    def create(self, values_list):
        self._check_workflow_access()
        clean_values_list = []
        for values in values_list:
            values = dict(values)
            year = values.get("pay_year")
            if year and 0 < year < 100:
                values["pay_year"] = 2000 + year
            clean_values_list.append(values)
        return super().create(clean_values_list)

    def write(self, values):
        self._check_workflow_access()
        internal_write = (
            self.env.context.get("_tese_internal_write")
            is TESE_INTERNAL_WRITE_TOKEN
        )
        if not internal_write:
            changed_fields = set(values)
            accounting_input_fields = self._INPUT_FIELDS - {
                "attachment_id",
                "document_note",
            }
            if accounting_input_fields & changed_fields:
                immutable = self.filtered(
                    lambda payslip: (
                        payslip.state in {"to_post", "to_reconcile", "paid"}
                        or (
                            payslip.move_id
                            and payslip.move_id.state == "posted"
                        )
                    ),
                )
                if immutable:
                    raise UserError(_(
                        "Prepared accounting inputs cannot be changed after a "
                        "journal entry has been created. Correct the draft entry "
                        "through the controlled TESE workflow or create a new "
                        "payroll record.",
                    ))
            if {"attachment_id", "document_note"} & changed_fields:
                immutable_documents = self.filtered(
                    lambda payslip: (
                        payslip.state in {"to_reconcile", "paid"}
                        or (
                            payslip.move_id
                            and payslip.move_id.state == "posted"
                        )
                    ),
                )
                if immutable_documents:
                    raise UserError(_(
                        "The provider document cannot be changed after the "
                        "payroll journal entry has been posted.",
                    ))
        return super().write(values)

    def unlink(self):
        self._check_workflow_access()
        if self.filtered(
            lambda payslip: (
                payslip.move_id
                or payslip.salary_settlement_move_id
                or payslip.tese_settlement_move_id
            ),
        ):
            raise UserError(_(
                "A TESE payroll record linked to accounting history cannot be deleted.",
            ))
        return super().unlink()

    @api.constrains("employee_id", "company_id")
    def _check_employee_company(self):
        for payslip in self:
            if payslip.employee_id.company_id != payslip.company_id:
                raise ValidationError(_(
                    "The payroll record and employee must belong to the same company.",
                ))

    @api.constrains("attachment_id")
    def _check_pdf_attachment(self):
        for payslip in self.filtered("attachment_id"):
            if payslip.attachment_id.mimetype != "application/pdf":
                raise ValidationError(_("The payroll attachment must be a PDF."))

    def _notify(self, message, *, level="success"):
        self.ensure_one()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("TESE Payroll"),
                "message": message,
                "type": level,
                "sticky": level == "warning",
                "next": {
                    "type": "ir.actions.act_window",
                    "res_model": self._name,
                    "res_id": self.id,
                    "view_mode": "form",
                    "views": [(False, "form")],
                    "target": "current",
                },
            },
        }

    def _period_dates(self):
        self.ensure_one()
        year = self.pay_year
        if 0 < year < 100:
            year += 2000
        if not 2020 <= year <= 2100 or not 1 <= self.pay_month <= 12:
            raise ValidationError(_(
                "Enter a payroll month between 1 and 12 and a year between "
                "2020 and 2100.",
            ))
        return (
            date(year, self.pay_month, 1),
            date(year, self.pay_month, calendar.monthrange(year, self.pay_month)[1]),
        )

    def _select_profile(self, period_start, period_end):
        self.ensure_one()
        domain = [
            ("active", "=", True),
            ("company_id", "=", self.company_id.id),
            ("employee_id", "=", self.employee_id.id),
            "|",
            ("valid_from", "=", False),
            ("valid_from", "<=", period_end),
            "|",
            ("valid_to", "=", False),
            ("valid_to", ">=", period_start),
        ]
        profiles = self.env["usl.tese.profile"].search(domain)
        if self.profile_id:
            if self.profile_id not in profiles:
                raise ValidationError(_(
                    "The selected profile is inactive or outside the payroll period.",
                ))
            profiles = self.profile_id
        if len(profiles) != 1:
            raise ValidationError(_(
                "Payroll preparation requires exactly one active profile for "
                "%(employee)s in this period; %(count)s were found.",
                employee=self.employee_id.display_name,
                count=len(profiles),
            ))
        return profiles

    def _select_hr_version(self, profile, period_start, period_end):
        self.ensure_one()
        versions = self.employee_id.version_ids.filtered(
            lambda version: (
                version.active
                and version.date_start
                and version.date_start <= period_end
                and (not version.date_end or version.date_end >= period_start)
            ),
        )
        if profile.hr_version_id:
            if profile.hr_version_id not in versions:
                raise ValidationError(_(
                    "The profile's preferred employee record does not cover "
                    "the payroll period.",
                ))
            versions = profile.hr_version_id
        if len(versions) != 1:
            raise ValidationError(_(
                "Payroll preparation requires exactly one compatible employee "
                "record for %(employee)s; %(count)s were found.",
                employee=self.employee_id.display_name,
                count=len(versions),
            ))
        return versions

    def _preparation_totals(self):
        self.ensure_one()
        debit = sum(
            self.component_line_ids.filtered(
                lambda line: line.side == "debit",
            ).mapped("amount"),
        )
        credit = sum(
            self.component_line_ids.filtered(
                lambda line: line.side == "credit",
            ).mapped("amount"),
        )
        social = sum(
            self.component_line_ids.filtered(
                lambda line: line.role == "social",
            ).mapped("amount"),
        )
        income_tax = sum(
            self.component_line_ids.filtered(
                lambda line: line.role == "income_tax",
            ).mapped("amount"),
        )
        return debit, credit, social, income_tax

    def _validate_preparation(self):
        self.ensure_one()
        currency = self.currency_id
        errors = []
        codes = self.component_line_ids.mapped("code")
        missing = sorted(set(TESE_COMPONENT_CODES) - set(codes))
        duplicated = sorted({code for code in codes if codes.count(code) > 1})
        if missing:
            errors.append(_("missing components: %(codes)s", codes=", ".join(missing)))
        if duplicated:
            errors.append(_(
                "duplicated components: %(codes)s",
                codes=", ".join(duplicated),
            ))
        if len(self.component_line_ids) != len(TESE_COMPONENT_CODES):
            errors.append(_("the component snapshot must contain exactly 11 lines"))
        if self.component_line_ids.filtered(lambda line: not line.account_id):
            errors.append(_("one or more component accounts are missing"))
        non_reconcilable = self.component_line_ids.filtered(
            lambda line: (
                line.role in TESE_LIABILITY_ROLES
                and not line.account_id.reconcile
            ),
        )
        if non_reconcilable:
            errors.append(_(
                "liability accounts must allow reconciliation: %(accounts)s",
                accounts=", ".join(non_reconcilable.mapped("account_id.code")),
            ))

        debit, credit, _social, _income_tax = self._preparation_totals()
        if not currency.is_zero(debit - credit):
            errors.append(_(
                "the journal entry is not balanced (%(debit).2f debit / "
                "%(credit).2f credit)",
                debit=debit,
                credit=credit,
            ))
        if not currency.is_zero(
            self.gross_salary
            - self.employee_contribution_total
            - self.net_before_tax,
        ):
            errors.append(_(
                "gross minus employee contributions must equal net before tax",
            ))
        if not currency.is_zero(
            self.net_before_tax - self.income_tax_amount - self.net_paid,
        ):
            errors.append(_("net before tax minus withholding must equal net paid"))
        if not currency.is_zero(
            self.gross_salary + self.employer_contribution_total - debit,
        ):
            errors.append(_(
                "gross plus employer contributions must equal total debit",
            ))
        salary_lines = self.component_line_ids.filtered(
            lambda line: line.role == "salary",
        )
        if len(salary_lines) != 1 or not currency.is_zero(
            salary_lines.amount - self.net_paid,
        ):
            errors.append(_("the 421000 salary liability must equal net paid"))
        if not self.collector_partner_id:
            errors.append(_("the TESE collector is missing"))
        if not self.employee_partner_id:
            errors.append(_("the employee work contact is missing"))
        if errors:
            raise ValidationError(_(
                "Correct the TESE payroll before creating accounting:\n- %(errors)s",
                errors="\n- ".join(errors),
            ))
        return True

    def action_prepare(self):
        self.ensure_one()
        self._check_workflow_access()
        if self.state != "draft" or self.move_id:
            raise UserError(_(
                "Only a payroll draft without a journal entry can be prepared.",
            ))
        period_start, period_end = self._period_dates()
        profile = self._select_profile(period_start, period_end)
        profile._validate_components()
        version = self._select_hr_version(profile, period_start, period_end)
        payment_date = self.payment_date or period_end + relativedelta(days=1)
        tese_payment_date = (
            self.tese_payment_date
            or payment_date + relativedelta(months=1, day=15)
        )
        hr_monthly_hours = version.hours_per_week * 52.0 / 12.0
        component_commands = [Command.clear()]
        snapshot_lines = []
        for line in profile.component_line_ids.sorted("sequence"):
            component_commands.append(Command.create({
                "sequence": line.sequence,
                "code": line.code,
                "name": line.name,
                "side": line.side,
                "role": line.role,
                "account_id": line.account_id.id,
                "amount": line.amount,
                "profile_line_id": line.id,
            }))
            snapshot_lines.append(
                f"{line.code} | {line.account_id.display_name} | "
                f"{line.side} | {line.amount:.2f}",
            )
        values = {
            "profile_id": profile.id,
            "hr_version_id": version.id,
            "collector_partner_id": (
                profile.collector_partner_id.id
                or self.company_id.tese_collector_partner_id.id
            ),
            "pay_year": period_end.year,
            "period_start": period_start,
            "period_end": period_end,
            "payment_date": payment_date,
            "payslip_date": self.payslip_date or period_end,
            "tese_payment_date": tese_payment_date,
            "hours": profile.default_hours,
            "gross_salary": profile.gross_salary,
            "employee_contribution_total": profile.employee_contribution_total,
            "employer_contribution_total": profile.employer_contribution_total,
            "net_social": profile.net_social,
            "net_before_tax": profile.net_before_tax,
            "income_tax_base": profile.income_tax_base,
            "income_tax_rate": profile.income_tax_rate,
            "income_tax_amount": profile.income_tax_amount,
            "net_paid": profile.net_paid,
            "component_line_ids": component_commands,
            "profile_snapshot_label": profile.display_name,
            "profile_snapshot_text": "\n".join(snapshot_lines),
            "employee_snapshot_name": self.employee_id.name,
            "employee_partner_snapshot_id": self.employee_partner_id.id,
            "hr_wage_snapshot": version.wage,
            "hr_hours_snapshot": hr_monthly_hours,
            "profile_valid_from_snapshot": profile.valid_from,
            "profile_valid_to_snapshot": profile.valid_to,
        }
        self.with_context(
            _tese_internal_write=TESE_INTERNAL_WRITE_TOKEN,
        ).write(values)
        self._validate_preparation()
        debit, credit, social, income_tax = self._preparation_totals()
        bank_amount = self.tese_bank_amount or social + income_tax
        bank_difference = bank_amount - social - income_tax
        warning_lines = []
        if not self.currency_id.is_zero(self.gross_salary - version.wage):
            warning_lines.append(_(
                "Provider gross %(provider).2f differs from HR wage %(hr).2f.",
                provider=self.gross_salary,
                hr=version.wage,
            ))
        if self.hours and hr_monthly_hours and abs(self.hours - hr_monthly_hours) > 0.01:
            warning_lines.append(_(
                "Provider hours %(provider).2f differ from HR monthly hours "
                "%(hr).2f.",
                provider=self.hours,
                hr=hr_monthly_hours,
            ))
        period_label = format_date(
            self.env,
            period_end,
            date_format="MMMM y",
        )
        name = _(
            "TESE Payroll %(employee)s — %(period)s",
            employee=self.employee_id.name,
            period=period_label,
        )
        checklist = "\n".join([
            _("Total debit: %(amount).2f", amount=debit),
            _("Total credit: %(amount).2f", amount=credit),
            _("TESE detailed total: %(amount).2f", amount=social + income_tax),
            _("TESE bank amount: %(amount).2f", amount=bank_amount),
            _("TESE difference: %(amount).2f", amount=bank_difference),
        ])
        message = _(
            "%(period)s prepared. Net salary: %(salary).2f; TESE collection: "
            "%(tese).2f. Create the draft journal entry next.",
            period=period_label,
            salary=self.net_paid,
            tese=bank_amount,
        )
        self.with_context(
            _tese_internal_write=TESE_INTERNAL_WRITE_TOKEN,
        ).write({
            "name": name,
            "state": "prepared",
            "total_debit": debit,
            "total_credit": credit,
            "balance_difference": debit - credit,
            "tese_contribution_total": social,
            "tese_income_tax_total": income_tax,
            "tese_detailed_total": social + income_tax,
            "tese_bank_amount": bank_amount,
            "tese_bank_difference": bank_difference,
            "preparation_ok": True,
            "preparation_message": message,
            "control_checklist": checklist,
            "preparation_warnings": "\n".join(warning_lines),
            "bank_reconcile_message": message,
        })
        profile.sudo().write({
            "last_used_date": period_end,
        })
        return self._notify(message)

    def _move_reference(self):
        self.ensure_one()
        return _(
            "TESE Payroll - %(employee)s - %(period)s - %(reference)s",
            employee=self.employee_snapshot_name or self.employee_id.name,
            period=format_date(self.env, self.period_end, date_format="MMMM y"),
            reference=self.tese_reference,
        )

    def _payroll_move_line_commands(self):
        self.ensure_one()
        label = self._move_reference()
        commands = [Command.clear()]
        for component in self.component_line_ids.sorted("sequence"):
            if self.currency_id.is_zero(component.amount):
                continue
            partner = (
                self.employee_partner_snapshot_id
                if component.role
                in {"gross", "employer_contribution", "salary"}
                else self.collector_partner_id
            )
            commands.append(Command.create({
                "name": label,
                "account_id": component.account_id.id,
                "partner_id": partner.id,
                "debit": component.amount if component.side == "debit" else 0.0,
                "credit": component.amount if component.side == "credit" else 0.0,
            }))
        return commands

    def action_create_draft_entry(self):
        self.ensure_one()
        self._check_workflow_access()
        if self.state not in {"prepared", "to_post"}:
            raise UserError(_(
                "A draft entry can only be created or refreshed from a prepared payroll.",
            ))
        self._validate_preparation()
        journal = self.company_id.tese_payroll_journal_id
        if not journal:
            raise UserError(_(
                "Configure a general TESE Payroll Journal on the company first.",
            ))
        if journal.type != "general" or journal.company_id != self.company_id:
            raise UserError(_(
                "The TESE Payroll Journal must be a general journal for this company.",
            ))
        move_values = {
            "move_type": "entry",
            "company_id": self.company_id.id,
            "journal_id": journal.id,
            "date": self.period_end,
            "ref": self._move_reference(),
            "tese_payslip_id": self.id,
            "tese_move_role": "payroll",
            "tese_attachment_id": self.attachment_id.id,
            "line_ids": self._payroll_move_line_commands(),
        }
        if self.move_id:
            if self.move_id.state != "draft":
                raise UserError(_(
                    "The linked payroll entry is no longer a draft and cannot be updated.",
                ))
            self.move_id.write(move_values)
            move = self.move_id
            message = _("The draft payroll journal entry was refreshed.")
        else:
            move = self.env["account.move"].create(move_values)
            message = _("The draft payroll journal entry was created.")
        self.with_context(
            _tese_internal_write=TESE_INTERNAL_WRITE_TOKEN,
        ).write({
            "move_id": move.id,
            "move_ref": move.ref,
            "state": "to_post",
            "preparation_message": message,
            "bank_reconcile_message": message,
        })
        return self._notify(message)

    def action_post(self):
        self.ensure_one()
        self._check_workflow_access()
        if self.state != "to_post" or not self.move_id:
            raise UserError(_("Create the draft payroll entry before posting it."))
        if not self.attachment_id:
            raise UserError(_("Attach the provider payroll PDF before posting."))
        self.move_id.action_post()
        message = _(
            "Payroll posted. Reconcile the salary payment and TESE collection next.",
        )
        self.with_context(
            _tese_internal_write=TESE_INTERNAL_WRITE_TOKEN,
        ).write({
            "state": "to_reconcile",
            "bank_reconcile_message": message,
        })
        return self._notify(message)

    def _debt_lines(self, kind):
        self.ensure_one()
        roles = {"salary"} if kind == "salary" else {"social", "income_tax"}
        account_ids = self.component_line_ids.filtered(
            lambda component: component.role in roles,
        ).account_id.ids
        return self.move_id.line_ids.filtered(
            lambda line: (
                line.account_id.id in account_ids
                and line.credit > 0
            ),
        )

    def _residual_status(self):
        self.ensure_one()
        salary_lines = self._debt_lines("salary")
        tese_lines = self._debt_lines("tese")
        salary_open = sum(abs(line.amount_residual) for line in salary_lines)
        tese_open = sum(abs(line.amount_residual) for line in tese_lines)
        salary_ok = bool(salary_lines) and all(
            line.reconciled or self.currency_id.is_zero(line.amount_residual)
            for line in salary_lines
        )
        tese_ok = bool(tese_lines) and all(
            line.reconciled or self.currency_id.is_zero(line.amount_residual)
            for line in tese_lines
        )
        return salary_ok, tese_ok, salary_open, tese_open

    def _candidate_label(self, line):
        return " ".join(filter(None, [
            line.name,
            line.ref,
            line.move_id.ref,
            line.move_id.name,
            line.partner_id.display_name,
        ]))

    def _candidate_is_safe(self, kind, line, expected_date):
        label = _normalized(self._candidate_label(line))
        if kind == "salary":
            partner = self.employee_partner_snapshot_id
            identity_tokens = {
                _normalized(self.employee_snapshot_name),
                _normalized(self.tese_reference),
            }
            max_days = 45
        else:
            partner = self.collector_partner_id
            identity_tokens = {
                "tese",
                "urssaf",
                _normalized(self.tese_reference),
            }
            max_days = 90
        identity_match = (
            bool(partner and line.partner_id == partner)
            or any(token and token in label for token in identity_tokens)
        )
        return (
            identity_match
            and bool(line.date and expected_date)
            and abs((line.date - expected_date).days) <= max_days
        )

    def _rank_candidates(self, kind):
        self.ensure_one()
        if kind == "salary":
            expected = self.net_paid
            expected_date = self.payment_date
            partner = self.employee_partner_snapshot_id
            tokens = {_normalized(self.employee_snapshot_name)}
        else:
            expected = self.tese_bank_amount
            expected_date = self.tese_payment_date
            partner = self.collector_partner_id
            tokens = {"tese", "urssaf"}
        if not expected_date or self.currency_id.is_zero(expected):
            return []
        date_from = expected_date - timedelta(days=90)
        date_to = expected_date + timedelta(days=90)
        lines = self.env["account.move.line"].search([
            ("company_id", "=", self.company_id.id),
            ("move_id.state", "=", "posted"),
            ("journal_id.type", "in", ("bank", "cash")),
            ("date", ">=", date_from),
            ("date", "<=", date_to),
            ("balance", ">", 0),
            ("reconciled", "=", False),
            ("account_id.reconcile", "=", True),
            ("move_id", "!=", self.move_id.id),
        ])
        ranked = []
        for line in lines:
            if line.currency_id and line.currency_id != self.currency_id:
                continue
            residual = abs(line.amount_residual)
            difference = residual - expected
            absolute_difference = abs(difference)
            if absolute_difference > 5.0:
                continue
            exact = self.currency_id.is_zero(difference)
            score = 100 if exact else 60 if absolute_difference <= 1 else 20
            day_difference = abs((line.date - expected_date).days)
            score += 50 if day_difference <= 3 else 30 if day_difference <= 10 else 10 if day_difference <= 31 else -20
            label = _normalized(self._candidate_label(line))
            if partner and line.partner_id == partner:
                score += 60
            elif any(token and token in label for token in tokens):
                score += 30
            if _normalized(self.tese_reference) in label:
                score += 20
            fingerprint = hashlib.sha256(
                "|".join(map(str, [
                    line.id,
                    line.write_date,
                    residual,
                    line.partner_id.id,
                    expected,
                    expected_date,
                ])).encode(),
            ).hexdigest()
            ranked.append({
                "line": line,
                "amount": residual,
                "difference": difference,
                "exact": exact,
                "safe": exact and self._candidate_is_safe(
                    kind,
                    line,
                    expected_date,
                ),
                "score": score,
                "fingerprint": fingerprint,
            })
        return sorted(
            ranked,
            key=lambda candidate: (
                -candidate["score"],
                abs(candidate["difference"]),
                candidate["line"].date,
                candidate["line"].id,
            ),
        )

    def _candidate_values(self, kind, ranked):
        prefix = "salary_payment" if kind == "salary" else "tese_payment"
        best = ranked[0] if ranked else False
        values = {
            f"{prefix}_candidate_count": len(ranked),
            f"{prefix}_match_score": best["score"] if best else 0,
            f"{prefix}_best_line_id": best["line"].id if best else False,
            f"{prefix}_candidate_date": best["line"].date if best else False,
            f"{prefix}_candidate_amount": best["amount"] if best else 0,
            f"{prefix}_candidate_difference": (
                best["difference"] if best else 0
            ),
            f"{prefix}_candidate_label": (
                self._candidate_label(best["line"]) if best else False
            ),
        }
        exact = [candidate for candidate in ranked if candidate["exact"]]
        safe = [candidate for candidate in exact if candidate["safe"]]
        if not ranked:
            message = _("No candidate found.")
        elif len(exact) > 1:
            message = _(
                "%(count)s exact candidates found. Review them in Bank Matching.",
                count=len(exact),
            )
        elif not safe:
            message = _(
                "The best candidate is not a unique exact safe match. Review it "
                "in Bank Matching.",
            )
        else:
            message = _(
                "One unique exact safe candidate is available for reconciliation.",
            )
        values[f"{prefix}_match_message"] = message
        return values

    def action_refresh_candidates(self):
        self.ensure_one()
        self._check_workflow_access()
        if not self.move_id or self.move_id.state != "posted":
            raise UserError(_("Post the payroll journal entry first."))
        salary_ok, tese_ok, _salary_open, _tese_open = self._residual_status()
        salary_ranked = [] if salary_ok else self._rank_candidates("salary")
        tese_ranked = [] if tese_ok else self._rank_candidates("tese")
        values = {
            **self._candidate_values("salary", salary_ranked),
            **self._candidate_values("tese", tese_ranked),
            "salary_payment_reconciled": salary_ok,
            "tese_payment_reconciled": tese_ok,
            "state": "paid" if salary_ok and tese_ok else "to_reconcile",
        }
        values["bank_reconcile_message"] = _(
            "Candidates refreshed. Salary: %(salary)s TESE: %(tese)s",
            salary=values["salary_payment_match_message"],
            tese=values["tese_payment_match_message"],
        )
        self.with_context(
            _tese_internal_write=TESE_INTERNAL_WRITE_TOKEN,
        ).write(values)
        return self._notify(values["bank_reconcile_message"])

    def _unique_safe_exact_candidate(self, kind):
        self.ensure_one()
        ranked = self._rank_candidates(kind)
        exact = [candidate for candidate in ranked if candidate["exact"]]
        safe = [candidate for candidate in exact if candidate["safe"]]
        if len(exact) != 1 or len(safe) != 1:
            raise UserError(_(
                "Automatic reconciliation requires one unique exact safe "
                "candidate. Refresh suggestions and use Bank Matching for "
                "differences or ambiguity.",
            ))
        candidate = safe[0]
        line = candidate["line"].exists()
        if (
            not line
            or line.reconciled
            or line.move_id.state != "posted"
            or not line.account_id.reconcile
            or not self.currency_id.is_zero(
                abs(line.amount_residual) - candidate["amount"],
            )
        ):
            raise UserError(_(
                "The candidate changed or was reconciled. Refresh suggestions.",
            ))
        return candidate

    def _create_settlement_bridge(self, kind, candidate, debt_lines):
        self.ensure_one()
        line = candidate["line"]
        journal = self.company_id.tese_payroll_journal_id
        if not journal:
            raise UserError(_("Configure the TESE Payroll Journal first."))
        expected = self.net_paid if kind == "salary" else self.tese_bank_amount
        if kind == "tese" and not self.currency_id.is_zero(
            self.tese_bank_difference,
        ):
            raise UserError(_(
                "A TESE bank difference cannot be bridged automatically. Use "
                "Bank Matching and document the rounding or partial amount.",
            ))
        if not all(
            component.account_id.reconcile
            for component in self.component_line_ids.filtered(
                lambda component: (
                    component.role == "salary"
                    if kind == "salary"
                    else component.role in {"social", "income_tax"}
                ),
            )
        ):
            raise UserError(_("Every settled liability account must be reconcilable."))
        debt_residual = sum(abs(item.amount_residual) for item in debt_lines)
        if not self.currency_id.is_zero(debt_residual - expected):
            raise UserError(_(
                "The current liability residual no longer equals the expected "
                "amount. Refresh and review the ledger.",
            ))
        label = _(
            "%(kind)s settlement — %(payroll)s",
            kind=_("Salary") if kind == "salary" else _("TESE"),
            payroll=self.name,
        )
        commands = []
        for debt_line in debt_lines:
            commands.append(Command.create({
                "name": label,
                "account_id": debt_line.account_id.id,
                "partner_id": debt_line.partner_id.id,
                "debit": abs(debt_line.amount_residual),
                "credit": 0.0,
            }))
        commands.append(Command.create({
            "name": label,
            "account_id": line.account_id.id,
            "partner_id": line.partner_id.id,
            "debit": 0.0,
            "credit": expected,
        }))
        move = self.env["account.move"].create({
            "move_type": "entry",
            "company_id": self.company_id.id,
            "journal_id": journal.id,
            "date": line.date,
            "ref": label,
            "tese_payslip_id": self.id,
            "tese_move_role": (
                "salary_settlement" if kind == "salary" else "tese_settlement"
            ),
            "line_ids": commands,
        })
        move.action_post()
        bridge_credit = move.line_ids.filtered(
            lambda item: item.account_id == line.account_id and item.credit > 0,
        )
        if len(bridge_credit) != 1:
            raise UserError(_("The settlement bridge suspense line is invalid."))
        (line + bridge_credit).reconcile()
        for account in debt_lines.account_id:
            original = debt_lines.filtered(lambda item: item.account_id == account)
            bridge = move.line_ids.filtered(
                lambda item: item.account_id == account and item.debit > 0,
            )
            (original + bridge).reconcile()
        return move

    def _reconcile_candidate(self, kind):
        self.ensure_one()
        self._check_workflow_access()
        if not self.move_id or self.move_id.state != "posted":
            raise UserError(_("Post the payroll journal entry first."))
        candidate = self._unique_safe_exact_candidate(kind)
        debt_lines = self._debt_lines(kind).filtered(
            lambda line: (
                not line.reconciled
                and not self.currency_id.is_zero(line.amount_residual)
            ),
        )
        if not debt_lines:
            raise UserError(_("The selected liability is already settled."))
        line = candidate["line"]
        if (
            kind == "salary"
            and len(debt_lines) == 1
            and line.account_id == debt_lines.account_id
        ):
            (debt_lines + line).reconcile()
            settlement_move = False
        else:
            settlement_move = self._create_settlement_bridge(
                kind,
                candidate,
                debt_lines,
            )
        field_name = (
            "salary_settlement_move_id"
            if kind == "salary"
            else "tese_settlement_move_id"
        )
        if settlement_move:
            self.with_context(
                _tese_internal_write=TESE_INTERNAL_WRITE_TOKEN,
            ).write({
                field_name: settlement_move.id,
            })
        self.action_refresh_candidates()
        self.action_finalize()
        return self._notify(
            _("The %(kind)s payment was reconciled.", kind=kind.upper()),
        )

    def action_reconcile_salary(self):
        return self._reconcile_candidate("salary")

    def action_reconcile_tese(self):
        return self._reconcile_candidate("tese")

    def action_finalize(self):
        self.ensure_one()
        self._check_workflow_access()
        if not self.move_id or self.move_id.state != "posted":
            raise UserError(_("The payroll journal entry must be posted."))
        salary_ok, tese_ok, salary_open, tese_open = self._residual_status()
        if salary_ok and tese_ok:
            state = "paid"
            message = _(
                "Payroll finalized: salary and TESE liabilities are fully settled.",
            )
        else:
            state = "to_reconcile"
            message = _(
                "Payroll remains open: salary residual %(salary).2f; TESE "
                "residual %(tese).2f.",
                salary=salary_open,
                tese=tese_open,
            )
        self.with_context(
            _tese_internal_write=TESE_INTERNAL_WRITE_TOKEN,
        ).write({
            "state": state,
            "payment_check_ok": salary_ok and tese_ok,
            "payment_check_message": message,
            "salary_payment_reconciled": salary_ok,
            "tese_payment_reconciled": tese_ok,
            "bank_reconcile_message": message,
        })
        return salary_ok and tese_ok

    def action_cancel(self):
        self.ensure_one()
        self._check_workflow_access()
        if self.move_id and self.move_id.state == "posted":
            raise UserError(_(
                "A posted payroll entry cannot be cancelled or reset from TESE "
                "Payroll. Use an explicit accounting reversal and retain this history.",
            ))
        if self.salary_settlement_move_id or self.tese_settlement_move_id:
            raise UserError(_("A payroll with settlement entries cannot be cancelled."))
        if self.move_id and self.move_id.state == "draft":
            self.move_id.button_cancel()
        self.with_context(
            _tese_internal_write=TESE_INTERNAL_WRITE_TOKEN,
        ).write({"state": "cancelled"})
        return True

    def action_reset_to_prepared(self):
        self.ensure_one()
        self._check_workflow_access()
        if self.state != "cancelled":
            raise UserError(_("Only a cancelled payroll can be reset."))
        if self.move_id:
            if self.move_id.state != "cancel":
                raise UserError(_("The linked entry prevents resetting this payroll."))
            self.move_id.button_draft()
        self.with_context(
            _tese_internal_write=TESE_INTERNAL_WRITE_TOKEN,
        ).write({
            "state": "prepared",
        })
        return True

    def action_open_move(self):
        self.ensure_one()
        if not self.move_id:
            raise UserError(_("No payroll journal entry is linked."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Payroll Journal Entry"),
            "res_model": "account.move",
            "res_id": self.move_id.id,
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "current",
        }

    def action_open_employee(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Employee"),
            "res_model": "hr.employee",
            "res_id": self.employee_id.id,
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "current",
        }

    def action_open_matching_lines(self):
        self.ensure_one()
        ids = (
            self.move_id.line_ids
            | self.salary_payment_best_line_id
            | self.tese_payment_best_line_id
            | self.salary_settlement_move_id.line_ids
            | self.tese_settlement_move_id.line_ids
        ).ids
        return {
            "type": "ir.actions.act_window",
            "name": _("TESE Payroll Matching Items"),
            "res_model": "account.move.line",
            "view_mode": "list,form",
            "domain": [("id", "in", ids)],
            "context": {"create": False},
        }

    def action_open_bank_matching(self):
        self.ensure_one()
        best = self.tese_payment_best_line_id or self.salary_payment_best_line_id
        if best.statement_line_id:
            return best.statement_line_id.action_rebuild_open_bank_matching()
        journal = best.journal_id if best else False
        if journal and hasattr(journal, "action_rebuild_open_bank_matching"):
            return journal.action_rebuild_open_bank_matching()
        raise UserError(_(
            "Refresh candidates first, then open the relevant bank journal's "
            "Bank Matching workspace.",
        ))

    def action_open_pdf(self):
        self.ensure_one()
        if not self.attachment_id:
            raise UserError(_("No payroll PDF is linked."))
        return {
            "type": "ir.actions.act_url",
            "url": f"/web/content/{self.attachment_id.id}?download=0",
            "target": "new",
        }


class UslTesePayslipLine(models.Model):
    _name = "usl.tese.payslip.line"
    _description = "TESE Payroll Accounting Snapshot"
    _order = "sequence, id"
    _check_company_auto = True

    payslip_id = fields.Many2one(
        "usl.tese.payslip",
        required=True,
        ondelete="cascade",
        index=True,
    )
    profile_line_id = fields.Many2one(
        "usl.tese.profile.line",
        readonly=True,
        ondelete="restrict",
    )
    company_id = fields.Many2one(
        related="payslip_id.company_id",
        store=True,
        index=True,
    )
    currency_id = fields.Many2one(
        related="payslip_id.currency_id",
        readonly=True,
    )
    sequence = fields.Integer(default=10, readonly=True)
    code = fields.Char(required=True, readonly=True, index=True)
    name = fields.Char(required=True, readonly=True)
    side = fields.Selection(
        [("debit", "Debit"), ("credit", "Credit")],
        required=True,
        readonly=True,
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
        readonly=True,
        index=True,
    )
    account_id = fields.Many2one(
        "account.account",
        required=True,
        check_company=True,
        readonly=True,
    )
    amount = fields.Monetary(required=True, readonly=True)

    _payslip_code_unique = models.Constraint(
        "UNIQUE(payslip_id, code)",
        "A component code can only appear once in a payroll snapshot.",
    )

    def _check_workflow_access(self):
        self.env["usl.tese.payslip"]._check_workflow_access()

    @api.model_create_multi
    def create(self, values_list):
        self._check_workflow_access()
        payslips = self.env["usl.tese.payslip"].browse(
            {values.get("payslip_id") for values in values_list},
        ).exists()
        internal_write = (
            self.env.context.get("_tese_internal_write")
            is TESE_INTERNAL_WRITE_TOKEN
        )
        if not internal_write and payslips.filtered(
            lambda payslip: payslip.state != "draft",
        ):
            raise UserError(_("Payroll snapshots are immutable after preparation."))
        return super().create(values_list)

    def write(self, values):
        self._check_workflow_access()
        if (
            self.env.context.get("_tese_internal_write")
            is not TESE_INTERNAL_WRITE_TOKEN
        ):
            raise UserError(_("Payroll accounting snapshots are immutable."))
        return super().write(values)

    def unlink(self):
        self._check_workflow_access()
        internal_write = (
            self.env.context.get("_tese_internal_write")
            is TESE_INTERNAL_WRITE_TOKEN
        )
        if not internal_write and self.filtered(
            lambda line: line.payslip_id.state != "draft",
        ):
            raise UserError(_("Payroll accounting snapshots are immutable."))
        return super().unlink()
