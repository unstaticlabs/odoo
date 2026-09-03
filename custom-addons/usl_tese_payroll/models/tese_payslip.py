import hashlib
import unicodedata
from datetime import timedelta

from dateutil.relativedelta import relativedelta

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import format_amount, format_date

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
    _inherit = ["mail.thread", "mail.activity.mixin", "usl.document.link.mixin"]
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
            ("paid", "Settled"),
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
    pay_period = fields.Date(
        string="Payroll Month",
        required=True,
        default=lambda self: self._default_pay_period(),
        tracking=True,
        index=True,
    )
    period_start = fields.Date(
        compute="_compute_period_dates",
        store=True,
        readonly=True,
        copy=False,
        index=True,
    )
    period_end = fields.Date(
        compute="_compute_period_dates",
        store=True,
        readonly=True,
        copy=False,
        index=True,
    )
    period_label = fields.Char(compute="_compute_period_label")
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
        check_company=True,
        domain=(
            "[('mimetype', '=', 'application/pdf'), "
            "('company_id', '=', company_id)]"
        ),
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
        string="Expected / Matched Bank Debit",
        tracking=True,
        copy=False,
        help=(
            "Before posting, enter the collection amount announced by TESE only "
            "when it differs from the declared liabilities. After matching, this "
            "field records the real bank debit. A safe difference is carried "
            "forward on 431000; it is never written off automatically."
        ),
    )
    tese_bank_difference = fields.Monetary(readonly=True, copy=False)
    salary_open_amount = fields.Monetary(
        compute="_compute_payment_summary",
        store=True,
        readonly=True,
    )
    tese_open_amount = fields.Monetary(
        string="URSSAF Open",
        compute="_compute_payment_summary",
        store=True,
        readonly=True,
    )
    rounding_open_amount = fields.Monetary(
        string="URSSAF Carry-over",
        compute="_compute_payment_summary",
        store=True,
        readonly=True,
    )
    rounding_carryover_message = fields.Char(
        string="Carry-over Note",
        compute="_compute_rounding_carryover_message",
    )
    payment_status = fields.Selection(
        [
            ("not_posted", "Not posted"),
            ("open_both", "Salary and URSSAF open"),
            ("salary_open", "Salary open"),
            ("tese_open", "URSSAF open"),
            ("paid", "Settled"),
            ("cancelled", "Cancelled"),
        ],
        compute="_compute_payment_summary",
        store=True,
        readonly=True,
        index=True,
    )

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
    can_configure = fields.Boolean(compute="_compute_can_configure")
    setup_message = fields.Char(compute="_compute_setup_message")
    profile_mismatch_warning = fields.Text(
        string="TESE / HR Difference",
        related="profile_id.hr_mismatch_warning",
        readonly=True,
    )

    _period_employee_unique = models.Constraint(
        "UNIQUE(company_id, employee_id, pay_period)",
        "An employee can only have one TESE payroll record for a month.",
    )
    _reference_unique = models.Constraint(
        "UNIQUE(company_id, tese_reference)",
        "A TESE reference can only be used once per company.",
    )
    _period_first_day = models.Constraint(
        "CHECK(EXTRACT(DAY FROM pay_period) = 1)",
        "The payroll month must be stored on its first day.",
    )

    _INPUT_FIELDS = {
        "company_id",
        "profile_id",
        "employee_id",
        "pay_period",
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

    @api.depends_context("uid")
    def _compute_can_configure(self):
        allowed = self.env.su or (
            self.env.user.has_group("hr.group_hr_manager")
            and self.env.user.has_group("account.group_account_manager")
        )
        for payslip in self:
            payslip.can_configure = allowed

    @api.model
    def _month_start(self, value):
        value = fields.Date.to_date(value)
        return value.replace(day=1) if value else False

    @api.model
    def _default_pay_period(self):
        today = fields.Date.context_today(self)
        return today.replace(day=1) - relativedelta(months=1)

    @api.model
    def _suggest_pay_period(self, employee, company=None, today=None):
        company = company or self.env.company
        today_month = (
            fields.Date.to_date(today) or fields.Date.context_today(self)
        ).replace(day=1)
        last_completed_month = today_month - relativedelta(months=1)
        if not employee:
            return last_completed_month

        profiles = self.env["usl.tese.profile"].sudo().with_context(
            active_test=False,
        ).search([
            ("company_id", "=", company.id),
            ("employee_id", "=", employee.id),
        ])
        payrolls = self.sudo().with_context(active_test=False).search([
            ("company_id", "=", company.id),
            ("employee_id", "=", employee.id),
        ])
        existing = {
            self._month_start(period)
            for period in payrolls.mapped("pay_period")
            if period
        }
        starts = [
            self._month_start(value)
            for value in profiles.mapped("valid_from")
            if value
        ]
        if existing:
            starts.append(min(existing))
        cursor = min(starts) if starts else last_completed_month
        cursor = min(cursor, last_completed_month)
        while cursor <= last_completed_month:
            if cursor not in existing:
                return cursor
            cursor += relativedelta(months=1)
        if existing:
            proposed = max(existing) + relativedelta(months=1)
            if proposed > today_month:
                raise UserError(self.env._(
                    "Payroll already exists through the current month. Open "
                    "the existing payroll instead.",
                ))
            return proposed
        return last_completed_month

    @api.model
    def _applicable_profiles(self, company, employee, pay_period):
        if not company or not employee or not pay_period:
            return self.env["usl.tese.profile"]
        period_start = self._month_start(pay_period)
        period_end = period_start + relativedelta(months=1, days=-1)
        profiles = self.env["usl.tese.profile"].with_context(
            active_test=False,
        ).search([
            ("company_id", "=", company.id),
            ("employee_id", "=", employee.id),
            "|",
            ("valid_from", "=", False),
            ("valid_from", "<=", period_end),
            "|",
            ("valid_to", "=", False),
            ("valid_to", ">=", period_start),
        ])
        active_profiles = profiles.filtered("active")
        return active_profiles if len(active_profiles) == 1 else profiles

    @api.model
    def _applicable_hr_versions(self, employee, pay_period):
        if not employee or not pay_period:
            return self.env["hr.version"]
        period_start = self._month_start(pay_period)
        period_end = period_start + relativedelta(months=1, days=-1)
        return employee.sudo().version_ids.filtered(
            lambda version: (
                version.active
                and version.date_start
                and version.date_start <= period_end
                and (not version.date_end or version.date_end >= period_start)
            ),
        )

    @api.model
    def _draft_profile_values(self, profile):
        if not profile:
            return {
                "collector_partner_id": False,
                "hours": 0.0,
                "gross_salary": 0.0,
                "employee_contribution_total": 0.0,
                "employer_contribution_total": 0.0,
                "net_social": 0.0,
                "net_before_tax": 0.0,
                "income_tax_base": 0.0,
                "income_tax_rate": 0.0,
                "income_tax_amount": 0.0,
                "net_paid": 0.0,
                "tese_contribution_total": 0.0,
                "tese_income_tax_total": 0.0,
                "tese_detailed_total": 0.0,
                "tese_bank_amount": 0.0,
                "tese_bank_difference": 0.0,
            }
        social = sum(
            profile.component_line_ids.filtered(
                lambda line: line.role == "social",
            ).mapped("amount"),
        )
        income_tax = sum(
            profile.component_line_ids.filtered(
                lambda line: line.role == "income_tax",
            ).mapped("amount"),
        )
        return {
            "collector_partner_id": (
                profile.collector_partner_id.id
                or profile.company_id.tese_collector_partner_id.id
            ),
            "hours": profile.default_hours,
            "gross_salary": profile.gross_salary,
            "employee_contribution_total": (
                profile.employee_contribution_total
            ),
            "employer_contribution_total": (
                profile.employer_contribution_total
            ),
            "net_social": profile.net_social,
            "net_before_tax": profile.net_before_tax,
            "income_tax_base": profile.income_tax_base,
            "income_tax_rate": profile.income_tax_rate,
            "income_tax_amount": profile.income_tax_amount,
            "net_paid": profile.net_paid,
            "tese_contribution_total": social,
            "tese_income_tax_total": income_tax,
            "tese_detailed_total": social + income_tax,
            "tese_bank_amount": social + income_tax,
            "tese_bank_difference": 0.0,
        }

    @api.model
    def default_get(self, field_names):
        values = super().default_get(field_names)
        company = self.env["res.company"].browse(
            values.get("company_id"),
        ).exists() or self.env.company
        employee = self.env["hr.employee"].browse(
            values.get("employee_id"),
        ).exists()
        if not employee:
            current_profiles = self.env["usl.tese.profile"].search([
                ("company_id", "=", company.id),
                ("active", "=", True),
            ])
            employees = current_profiles.mapped("employee_id")
            if len(employees) == 1:
                employee = employees
                values["employee_id"] = employee.id
        pay_period = self._month_start(values.get("pay_period"))
        if employee and "pay_period" in field_names:
            pay_period = self._suggest_pay_period(employee, company)
            values["pay_period"] = pay_period
        if employee and pay_period:
            profiles = self._applicable_profiles(
                company,
                employee,
                pay_period,
            )
            if len(profiles) == 1 and "profile_id" in field_names:
                values["profile_id"] = profiles.id
                values.update(self._draft_profile_values(profiles))
            versions = self._applicable_hr_versions(employee, pay_period)
            if len(versions) == 1 and "hr_version_id" in field_names:
                values["hr_version_id"] = versions.id
            period_end = pay_period + relativedelta(months=1, days=-1)
            payment_date = period_end + relativedelta(days=1)
            values.setdefault("payment_date", payment_date)
            values.setdefault(
                "tese_payment_date",
                pay_period + relativedelta(months=2, day=15),
            )
            values.setdefault("payslip_date", period_end)
            values.setdefault(
                "tese_reference",
                f"TESE {pay_period:%Y-%m} — {employee.name}",
            )
        return values

    @api.depends("pay_period")
    def _compute_period_dates(self):
        for payslip in self:
            period_start = self._month_start(payslip.pay_period)
            payslip.period_start = period_start
            payslip.period_end = (
                period_start + relativedelta(months=1, days=-1)
                if period_start
                else False
            )

    @api.depends("pay_period")
    def _compute_period_label(self):
        for payslip in self:
            payslip.period_label = (
                format_date(
                    payslip.env,
                    payslip.pay_period,
                    date_format="MMMM y",
                )
                if payslip.pay_period
                else _("Payroll month")
            )

    @api.depends("company_id", "employee_id", "pay_period", "profile_id")
    def _compute_setup_message(self):
        for payslip in self:
            if not payslip.employee_id or not payslip.pay_period:
                payslip.setup_message = _(
                    "Choose an employee and payroll month. Next: confirm the "
                    "recurring TESE settings proposed by Odoo.",
                )
                continue
            profiles = payslip._applicable_profiles(
                payslip.company_id,
                payslip.employee_id,
                payslip.pay_period,
            )
            if payslip.profile_id and payslip.profile_id not in profiles:
                payslip.setup_message = _(
                    "The selected TESE settings do not cover this month. Next: "
                    "choose a version that covers the month or revise the "
                    "recurring settings.",
                )
            elif len(profiles) != 1:
                payslip.setup_message = _(
                    "%(count)s TESE settings profiles cover this month; exactly "
                    "one is required. Next: correct their validity dates in "
                    "Payroll Profiles.",
                    count=len(profiles),
                )
            else:
                versions = payslip._applicable_hr_versions(
                    payslip.employee_id,
                    payslip.pay_period,
                )
                if len(versions) != 1:
                    payslip.setup_message = _(
                        "%(count)s contract versions cover this month; exactly "
                        "one is required. Next: correct the employee contract "
                        "version dates.",
                        count=len(versions),
                    )
                else:
                    payslip.setup_message = False

    @api.onchange("company_id", "employee_id")
    def _onchange_employee_guided_defaults(self):
        for payslip in self:
            if not payslip.employee_id:
                payslip.profile_id = False
                payslip.hr_version_id = False
                continue
            payslip.pay_period = payslip._suggest_pay_period(
                payslip.employee_id,
                payslip.company_id,
            )
            payslip._apply_period_defaults()

    @api.onchange("pay_period")
    def _onchange_pay_period(self):
        for payslip in self:
            payslip.pay_period = payslip._month_start(payslip.pay_period)
            payslip._apply_period_defaults()

    def _apply_period_defaults(self):
        for payslip in self:
            if not payslip.pay_period:
                continue
            profiles = payslip._applicable_profiles(
                payslip.company_id,
                payslip.employee_id,
                payslip.pay_period,
            )
            payslip.profile_id = profiles if len(profiles) == 1 else False
            payslip.update(
                payslip._draft_profile_values(payslip.profile_id),
            )
            versions = payslip._applicable_hr_versions(
                payslip.employee_id,
                payslip.pay_period,
            )
            payslip.hr_version_id = versions if len(versions) == 1 else False
            period_end = (
                payslip.pay_period + relativedelta(months=1, days=-1)
            )
            payslip.payment_date = period_end + relativedelta(days=1)
            payslip.tese_payment_date = (
                payslip.pay_period + relativedelta(months=2, day=15)
            )
            payslip.payslip_date = period_end
            if payslip.employee_id:
                payslip.tese_reference = (
                    f"TESE {payslip.pay_period:%Y-%m} — "
                    f"{payslip.employee_id.name}"
                )

    @api.depends("attachment_id", "attachment_id.mimetype", "state")
    def _compute_document_status(self):
        for payslip in self:
            if not payslip.attachment_id:
                payslip.document_status = "missing"
                payslip.document_message = _(
                    "No provider PDF is attached. Next: attach the official TESE "
                    "PDF before posting.",
                )
            elif payslip.attachment_id.mimetype != "application/pdf":
                payslip.document_status = "warning"
                payslip.document_message = _(
                    "The linked attachment is not a PDF. Next: replace it with "
                    "the official TESE PDF.",
                )
            elif (
                payslip.id
                and (
                    payslip.attachment_id.res_model != payslip._name
                    or payslip.attachment_id.res_id != payslip.id
                )
            ):
                payslip.document_status = "linked"
                payslip.document_message = (
                    _(
                        "The PDF is linked but is not yet the payroll record's "
                        "native attachment. Next: keep it as evidence and "
                        "continue the payroll review.",
                    )
                    if payslip.state in {"draft", "prepared", "to_post"}
                    else _(
                        "The official PDF remains linked as evidence. Next: no "
                        "document action is required.",
                    )
                )
            else:
                payslip.document_status = "ok"
                payslip.document_message = (
                    _(
                        "Provider payroll PDF ready. Next: review the figures and "
                        "continue the payroll workflow.",
                    )
                    if payslip.state in {"draft", "prepared", "to_post"}
                    else _(
                        "Provider payroll PDF ready. Next: no document action is "
                        "required.",
                    )
                )

    def _tracked_liability_lines(self, kind):
        self.ensure_one()
        roles = {"salary"} if kind == "salary" else {"social", "income_tax"}
        account_ids = self.component_line_ids.filtered(
            lambda component: component.role in roles,
        ).account_id.ids
        moves = (
            self.move_id
            | self.salary_settlement_move_id
            | self.tese_settlement_move_id
        ).filtered(lambda move: move.state == "posted")
        return moves.line_ids.filtered(
            lambda line: (
                line.account_id.id in account_ids
                and not self.currency_id.is_zero(line.amount_residual)
            ),
        )

    def _is_tese_carryover_only(self, tese_open):
        self.ensure_one()
        return (
            not self.currency_id.is_zero(tese_open)
            and not self.currency_id.is_zero(self.tese_bank_difference)
            and self.currency_id.is_zero(
                tese_open - abs(self.tese_bank_difference),
            )
        )

    def _tese_carryover_message(self):
        self.ensure_one()
        direction = _("credit") if self.tese_bank_difference > 0 else _("due")
        return _(
            "Payroll settled. URSSAF carry-over: %(amount)s %(direction)s on "
            "431000. No payroll action is needed.",
            amount=format_amount(
                self.env,
                abs(self.tese_bank_difference),
                self.currency_id,
            ),
            direction=direction,
        )

    @api.depends(
        "rounding_open_amount",
        "tese_bank_difference",
        "currency_id",
    )
    def _compute_rounding_carryover_message(self):
        for payslip in self:
            payslip.rounding_carryover_message = (
                payslip._tese_carryover_message()
                if not payslip.currency_id.is_zero(
                    payslip.rounding_open_amount,
                )
                else False
            )

    @api.depends(
        "state",
        "move_id.state",
        "move_id.line_ids.amount_residual",
        "salary_settlement_move_id.state",
        "salary_settlement_move_id.line_ids.amount_residual",
        "tese_settlement_move_id.state",
        "tese_settlement_move_id.line_ids.amount_residual",
        "component_line_ids.role",
        "component_line_ids.account_id",
        "tese_bank_difference",
    )
    def _compute_payment_summary(self):
        for payslip in self:
            salary_open = sum(
                abs(line.amount_residual)
                for line in payslip._tracked_liability_lines("salary")
            )
            tese_residual = sum(
                abs(line.amount_residual)
                for line in payslip._tracked_liability_lines("tese")
            )
            carryover_only = payslip._is_tese_carryover_only(tese_residual)
            rounding_open = tese_residual if carryover_only else 0.0
            tese_open = 0.0 if carryover_only else tese_residual

            payslip.salary_open_amount = salary_open
            payslip.tese_open_amount = tese_open
            payslip.rounding_open_amount = rounding_open
            if payslip.state == "cancelled":
                status = "cancelled"
            elif not payslip.move_id or payslip.move_id.state != "posted":
                status = "not_posted"
            elif (
                payslip.currency_id.is_zero(salary_open)
                and payslip.currency_id.is_zero(tese_open)
            ):
                status = "paid"
            elif (
                not payslip.currency_id.is_zero(salary_open)
                and not payslip.currency_id.is_zero(tese_open)
            ):
                status = "open_both"
            elif not payslip.currency_id.is_zero(salary_open):
                status = "salary_open"
            else:
                status = "tese_open"
            payslip.payment_status = status

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
            pay_period = self._month_start(
                values.get("pay_period") or self._default_pay_period(),
            )
            values["pay_period"] = pay_period
            period_end = pay_period + relativedelta(months=1, days=-1)
            payment_date = values.get(
                "payment_date",
                period_end + relativedelta(days=1),
            )
            values.setdefault("payslip_date", period_end)
            values.setdefault("payment_date", payment_date)
            values.setdefault(
                "tese_payment_date",
                pay_period + relativedelta(months=2, day=15),
            )
            if not values.get("tese_reference") and values.get("employee_id"):
                employee = self.env["hr.employee"].browse(
                    values["employee_id"],
                )
                values["tese_reference"] = (
                    f"TESE {pay_period:%Y-%m} — {employee.name}"
                )
            profile = self.env["usl.tese.profile"].with_context(
                active_test=False,
            ).browse(values.get("profile_id")).exists()
            for field_name, value in self._draft_profile_values(profile).items():
                values.setdefault(field_name, value)
            clean_values_list.append(values)
        return super().create(clean_values_list)

    def write(self, values):
        self._check_workflow_access()
        values = dict(values)
        if values.get("pay_period"):
            values["pay_period"] = self._month_start(values["pay_period"])
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

    @api.constrains("pay_period")
    def _check_pay_period(self):
        for payslip in self:
            if not payslip.pay_period:
                continue
            if payslip.pay_period.day != 1:
                raise ValidationError(_(
                    "Choose a payroll month, not an individual day.",
                ))
            if not 2020 <= payslip.pay_period.year <= 2100:
                raise ValidationError(_(
                    "The payroll month must be between 2020 and 2100.",
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
        period_start = self._month_start(self.pay_period)
        if not period_start:
            raise ValidationError(_("Choose a payroll month."))
        return (
            period_start,
            period_start + relativedelta(months=1, days=-1),
        )

    def _select_profile(self, period_start, period_end):
        self.ensure_one()
        profiles = self._applicable_profiles(
            self.company_id,
            self.employee_id,
            period_start,
        )
        if self.profile_id:
            if self.profile_id not in profiles:
                raise ValidationError(_(
                    "The selected TESE settings do not cover the payroll month.",
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
        versions = self._applicable_hr_versions(
            self.employee_id,
            period_start,
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
            or period_start + relativedelta(months=2, day=15)
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
            # This entry belongs exclusively to this payroll and is still a
            # draft. The controlled workflow has already checked the user's
            # combined HR/Accounting rights; sudo avoids unrelated optional
            # accounting extensions blocking their own access checks here.
            move = self.move_id.sudo()
            move.write(move_values)
            message = _(
                "The draft payroll journal entry was refreshed. Next: review "
                "the entry and official PDF, then post it.",
            )
        else:
            move = self.env["account.move"].create(move_values)
            message = _(
                "The draft payroll journal entry was created. Next: review the "
                "entry and official PDF, then post it.",
            )
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
        salary_lines = self._tracked_liability_lines("salary")
        tese_lines = self._tracked_liability_lines("tese")
        salary_open = sum(abs(line.amount_residual) for line in salary_lines)
        tese_open = sum(abs(line.amount_residual) for line in tese_lines)
        salary_ok = bool(self._debt_lines("salary")) and self.currency_id.is_zero(
            salary_open,
        )
        tese_ok = bool(self._debt_lines("tese")) and self.currency_id.is_zero(
            tese_open,
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
            detailed_total = expected
            expected_date = self.payment_date
            partner = self.employee_partner_snapshot_id
            tokens = {_normalized(self.employee_snapshot_name)}
        else:
            detailed_total = self.tese_detailed_total
            expected = self.tese_bank_amount or detailed_total
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
            settlement_difference = residual - detailed_total
            absolute_difference = abs(difference)
            if (
                absolute_difference > 5.0
                or (
                    kind == "tese"
                    and abs(settlement_difference) > 5.0
                )
            ):
                continue
            exact = self.currency_id.is_zero(difference)
            score = 100 if exact else 60 if absolute_difference <= 1 else 20
            day_difference = abs((line.date - expected_date).days)
            if day_difference <= 3:
                score += 50
            elif day_difference <= 10:
                score += 30
            elif day_difference <= 31:
                score += 10
            else:
                score -= 20
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
                "settlement_difference": settlement_difference,
                "exact": exact,
                "safe": (
                    (exact or kind == "tese")
                    and self._candidate_is_safe(
                        kind,
                        line,
                        expected_date,
                    )
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
                best["settlement_difference"]
                if best and kind == "tese"
                else best["difference"] if best else 0
            ),
            f"{prefix}_candidate_label": (
                self._candidate_label(best["line"]) if best else False
            ),
        }
        safe = [candidate for candidate in ranked if candidate["safe"]]
        if not ranked:
            message = _(
                "No candidate found. Next: check the expected date and amount, "
                "or open Bank Matching.",
            )
        elif len(safe) > 1:
            message = _(
                "%(count)s plausible candidates found. Review them in Bank Matching.",
                count=len(safe),
            )
        elif len(safe) != 1:
            message = _(
                "The best candidate is not a unique safe match. Review it "
                "in Bank Matching.",
            )
        elif (
            kind == "tese"
            and not self.currency_id.is_zero(
                safe[0]["settlement_difference"],
            )
        ):
            message = _(
                "One URSSAF debit is ready. Its %(difference).2f difference "
                "will be carried forward on 431000. Next: check the bank "
                "transaction, then match it.",
                difference=abs(safe[0]["settlement_difference"]),
            )
        else:
            message = _(
                "One unique exact safe candidate is available. Next: check it, "
                "then use the matching button above.",
            )
        values[f"{prefix}_match_message"] = message
        return values

    def action_refresh_candidates(self):
        self.ensure_one()
        self._check_workflow_access()
        if not self.move_id or self.move_id.state != "posted":
            raise UserError(_("Post the payroll journal entry first."))
        salary_ok, tese_ok, _salary_open, tese_open = self._residual_status()
        carryover_only = self._is_tese_carryover_only(tese_open)
        tese_settled = tese_ok or carryover_only
        salary_ranked = [] if salary_ok else self._rank_candidates("salary")
        tese_ranked = [] if tese_settled else self._rank_candidates("tese")
        values = {
            **self._candidate_values("salary", salary_ranked),
            **self._candidate_values("tese", tese_ranked),
            "salary_payment_reconciled": salary_ok,
            "tese_payment_reconciled": tese_settled,
            "state": "paid" if salary_ok and tese_settled else "to_reconcile",
        }
        if salary_ok:
            values["salary_payment_match_message"] = _("Salary payment matched.")
        if tese_settled:
            values["tese_payment_match_message"] = _("URSSAF debit matched.")
        if (
            salary_ok
            and carryover_only
        ):
            values["bank_reconcile_message"] = self._tese_carryover_message()
        else:
            values["bank_reconcile_message"] = _(
                "Payments refreshed. Salary: %(salary)s URSSAF: %(tese)s",
                salary=values["salary_payment_match_message"],
                tese=values["tese_payment_match_message"],
            )
        self.with_context(
            _tese_internal_write=TESE_INTERNAL_WRITE_TOKEN,
        ).write(values)
        return self._notify(values["bank_reconcile_message"])

    def _unique_safe_candidate(self, kind):
        self.ensure_one()
        ranked = self._rank_candidates(kind)
        safe = [
            candidate
            for candidate in ranked
            if candidate["safe"] and (candidate["exact"] or kind == "tese")
        ]
        if len(safe) != 1:
            raise UserError(_(
                "Automatic reconciliation requires one unique safe candidate. "
                "Refresh suggestions and use Bank Matching for ambiguity or "
                "larger differences.",
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
        expected = self.net_paid if kind == "salary" else candidate["amount"]
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
        declared = self.net_paid if kind == "salary" else self.tese_detailed_total
        if not self.currency_id.is_zero(debt_residual - declared):
            raise UserError(_(
                "The current liability residual no longer equals the expected "
                "amount. Refresh and review the ledger.",
            ))
        rounding_difference = expected - declared
        if kind == "tese" and abs(rounding_difference) > 5.0:
            raise UserError(_(
                "The URSSAF difference exceeds the €5 safety limit. Use Bank "
                "Matching.",
            ))
        label = _(
            "%(kind)s settlement — %(payroll)s",
            kind=_("Salary") if kind == "salary" else _("TESE"),
            payroll=self.name,
        )
        commands = []
        rounding_account = self.component_line_ids.filtered(
            lambda component: component.code == "431000",
        ).account_id
        for debt_line in debt_lines:
            amount = abs(debt_line.amount_residual)
            if (
                kind == "tese"
                and debt_line.account_id == rounding_account
                and rounding_difference < 0
            ):
                amount += rounding_difference
                if amount < 0:
                    raise UserError(_(
                        "The negative URSSAF difference exceeds the 431000 "
                        "liability. Use Bank Matching.",
                    ))
            if self.currency_id.is_zero(amount):
                continue
            commands.append(Command.create({
                "name": label,
                "account_id": debt_line.account_id.id,
                "partner_id": debt_line.partner_id.id,
                "debit": amount,
                "credit": 0.0,
            }))
        if kind == "tese" and rounding_difference > 0:
            if not rounding_account:
                raise UserError(_("The 431000 payroll account is missing."))
            commands.append(Command.create({
                "name": _("URSSAF rounding to clear — %(payroll)s", payroll=self.name),
                "account_id": rounding_account.id,
                "partner_id": self.collector_partner_id.id,
                "debit": rounding_difference,
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
        candidate = self._unique_safe_candidate(kind)
        debt_lines = self._debt_lines(kind).filtered(
            lambda line: (
                not line.reconciled
                and not self.currency_id.is_zero(line.amount_residual)
            ),
        )
        if not debt_lines:
            raise UserError(_("The selected liability is already settled."))
        if kind == "tese":
            bank_difference = (
                candidate["amount"] - self.tese_detailed_total
            )
            self.with_context(
                _tese_internal_write=TESE_INTERNAL_WRITE_TOKEN,
            ).write({
                "tese_bank_amount": candidate["amount"],
                "tese_bank_difference": bank_difference,
            })
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
        self.action_finalize(notify=False)
        if not self.currency_id.is_zero(self.rounding_open_amount):
            next_step = self._tese_carryover_message()
        elif self.state == "paid":
            next_step = _("The payroll is settled; no further action is required.")
        else:
            next_step = _("Next: match the remaining open payment.")
        return self._notify(
            _(
                "The %(kind)s payment was reconciled. %(next_step)s",
                kind=kind.upper(),
                next_step=next_step,
            ),
        )

    def action_reconcile_salary(self):
        return self._reconcile_candidate("salary")

    def action_reconcile_tese(self):
        return self._reconcile_candidate("tese")

    def action_finalize(self, notify=True):
        self.ensure_one()
        self._check_workflow_access()
        if not self.move_id or self.move_id.state != "posted":
            raise UserError(_("The payroll journal entry must be posted."))
        salary_ok, tese_ok, salary_open, tese_open = self._residual_status()
        carryover_only = self._is_tese_carryover_only(tese_open)
        tese_settled = tese_ok or carryover_only
        if salary_ok and tese_settled:
            state = "paid"
            message = (
                self._tese_carryover_message()
                if carryover_only
                else _(
                    "Payroll settled: salary and TESE liabilities are fully "
                    "paid. No further payment action is required.",
                )
            )
        else:
            state = "to_reconcile"
            message = _(
                "Payroll remains open: salary residual %(salary).2f; TESE "
                "residual %(tese).2f. Next: match a unique safe candidate or "
                "open Bank Matching.",
                salary=salary_open,
                tese=tese_open,
            )
        self.with_context(
            _tese_internal_write=TESE_INTERNAL_WRITE_TOKEN,
        ).write({
            "state": state,
            "payment_check_ok": salary_ok and tese_settled,
            "payment_check_message": message,
            "salary_payment_reconciled": salary_ok,
            "tese_payment_reconciled": tese_settled,
            "bank_reconcile_message": message,
        })
        if notify:
            return self._notify(
                message,
                level="success" if salary_ok and tese_ok else "warning",
            )
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
        return self._notify(
            _(
                "Payroll cancelled. Next: use Reset if you want to prepare it "
                "again.",
            ),
            level="warning",
        )

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
        return self._notify(
            _(
                "Payroll reset to Prepared. Next: review or refresh the draft "
                "journal entry.",
            ),
        )

    def action_open_settings_revision(self):
        self.ensure_one()
        self._check_workflow_access()
        if self.state in {"to_reconcile", "paid", "cancelled"}:
            raise UserError(_(
                "Posted, settled, or cancelled payrolls keep their original "
                "settings snapshot.",
            ))
        return {
            "type": "ir.actions.act_window",
            "name": _("Update payroll settings"),
            "res_model": "usl.tese.settings.revision.wizard",
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "new",
            "context": {
                "default_payslip_id": self.id,
                "default_profile_id": self.profile_id.id,
                "default_employee_id": self.employee_id.id,
                "default_effective_period": self.pay_period,
            },
        }

    def _apply_revised_settings(self, profile, version):
        self.ensure_one()
        self._check_workflow_access()
        if self.state not in {"draft", "prepared", "to_post"}:
            raise UserError(_(
                "Settings can only be applied before the payroll entry is posted.",
            ))
        draft_move = self.move_id
        if draft_move and draft_move.state != "draft":
            raise UserError(_(
                "The linked payroll entry is posted and cannot be refreshed.",
            ))
        self.with_context(
            _tese_internal_write=TESE_INTERNAL_WRITE_TOKEN,
        ).write({
            "state": "draft",
            "move_id": False,
            "profile_id": profile.id,
            "hr_version_id": version.id,
        })
        self.action_prepare()
        if draft_move:
            self.with_context(
                _tese_internal_write=TESE_INTERNAL_WRITE_TOKEN,
            ).write({
                "move_id": draft_move.id,
                "state": "prepared",
            })
            self.action_create_draft_entry()
        self.message_post(body=_(
            "Applied TESE settings %(profile)s and contract version "
            "%(version)s.",
            profile=profile.display_name,
            version=version.display_name,
        ))
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
        journal = best.journal_id if best else False
        statement_line = best.statement_line_id
        action = self.env["ir.actions.actions"]._for_xml_id(
            "account_reconcile_oca.action_bank_statement_line_reconcile",
        )
        if statement_line:
            domain = [("id", "=", statement_line.id)]
        elif journal:
            domain = [("journal_id", "=", journal.id)]
        else:
            domain = [("company_id", "=", self.company_id.id)]
        context = {
            "allowed_company_ids": [self.company_id.id],
            "create": False,
            "search_default_not_reconciled": True,
            "view_ref": (
                "account_reconcile_oca."
                "bank_statement_line_form_reconcile_view"
            ),
        }
        if journal:
            context.update({
                "active_id": journal.id,
                "active_ids": journal.ids,
                "active_model": journal._name,
                "default_journal_id": journal.id,
            })
        action.update({
            "name": _("Bank Matching — %(payroll)s", payroll=self.name),
            "domain": domain,
            "context": context,
        })
        return action

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
