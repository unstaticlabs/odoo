import unicodedata

from dateutil.relativedelta import relativedelta

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import format_amount, format_date

from .constants import (
    TESE_INTERNAL_WRITE_TOKEN,
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
    def create(self, vals_list):
        self._check_workflow_access()
        clean_values_list = []
        for values in vals_list:
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

    def write(self, vals):
        self._check_workflow_access()
        vals = dict(vals)
        if vals.get("pay_period"):
            vals["pay_period"] = self._month_start(vals["pay_period"])
        internal_write = (
            self.env.context.get("_tese_internal_write")
            is TESE_INTERNAL_WRITE_TOKEN
        )
        if not internal_write:
            changed_fields = set(vals)
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
        return super().write(vals)

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
    def create(self, vals_list):
        self._check_workflow_access()
        payslips = self.env["usl.tese.payslip"].browse(
            {values.get("payslip_id") for values in vals_list},
        ).exists()
        internal_write = (
            self.env.context.get("_tese_internal_write")
            is TESE_INTERNAL_WRITE_TOKEN
        )
        if not internal_write and payslips.filtered(
            lambda payslip: payslip.state != "draft",
        ):
            raise UserError(_("Payroll snapshots are immutable after preparation."))
        return super().create(vals_list)

    def write(self, vals):
        self._check_workflow_access()
        if (
            self.env.context.get("_tese_internal_write")
            is not TESE_INTERNAL_WRITE_TOKEN
        ):
            raise UserError(_("Payroll accounting snapshots are immutable."))
        return super().write(vals)

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
