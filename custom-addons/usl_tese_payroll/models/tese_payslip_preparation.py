"""Pay-period suggestion, profile selection, preparation totals and the draft accounting entry."""

from dateutil.relativedelta import relativedelta

from odoo import (
    Command,
    _,
    api,
    fields,
    models,
)
from odoo.exceptions import UserError, ValidationError
from odoo.tools import format_date

from .constants import (
    TESE_COMPONENT_CODES,
    TESE_INTERNAL_WRITE_TOKEN,
    TESE_LIABILITY_ROLES,
)


class UslTesePayslip(models.Model):
    _inherit = "usl.tese.payslip"

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
