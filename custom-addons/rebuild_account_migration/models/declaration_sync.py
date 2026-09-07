"""Rule applicability, calendar instances, deadline computation and declaration synchronization."""

from datetime import date, timedelta

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models

from .declaration import EU_COUNTRY_CODES, _month_end


class RebuildAccountDeclaration(models.Model):
    _inherit = "rebuild.account.declaration"

    @api.model
    def sync_for_company(self, company):
        company.ensure_one()
        if not company.rebuild_declaration_profile_active:
            return self.browse()
        today = fields.Date.context_today(self)
        current_start, current_end = company.rebuild_compute_fiscalyear_dates(today)
        periods = [(current_start, current_end)]
        if company.fiscalyear_lock_date:
            locked_start, locked_end = company.rebuild_compute_fiscalyear_dates(company.fiscalyear_lock_date)
            if (locked_start, locked_end) not in periods:
                periods.insert(0, (locked_start, locked_end))
        next_start, next_end = company.rebuild_compute_fiscalyear_dates(current_end + relativedelta(days=1))
        if (
            next_end <= today + relativedelta(months=18)
            and (next_start, next_end) not in periods
        ):
            periods.append((next_start, next_end))

        declarations = self.browse()
        for fiscal_start, fiscal_end in periods:
            for rule in self._rules_for_period(company, fiscal_end):
                if rule.period_basis != "fiscal_year":
                    continue
                if not self._rule_applies_to_profile(rule, company, fiscal_start, fiscal_end):
                    continue
                declarations |= self._sync_rule_instances(company, rule, fiscal_start, fiscal_end)

        horizon_start = min(start for start, _end in periods)
        horizon_end = max(end for _start, end in periods) + relativedelta(months=4)
        declarations |= self._sync_calendar_instances(company, horizon_start, horizon_end)
        self._retire_superseded_instances(company, declarations, horizon_start, horizon_end)
        declarations.action_refresh_preparation()
        return declarations

    @api.model
    def _sync_all_profiled_companies(self):
        """Idempotently reconcile governed definitions after a module upgrade."""
        declarations = self.browse()
        for company in self.env["res.company"].search([
            ("rebuild_declaration_profile_active", "=", True),
        ]):
            declarations |= self.sync_for_company(company)
        return declarations

    @api.model
    def _rules_for_period(self, company, fiscal_end):
        country = (
            company.account_fiscal_country_id
            or company.country_id
        )
        candidates = self.env[
            "rebuild.account.declaration.rule"
        ].with_context(active_test=False).search([
            ("country_id", "=", country.id),
            ("company_id", "in", [False, company.id]),
            ("effective_from", "<=", fiscal_end),
            "|",
            ("effective_to", "=", False),
            ("effective_to", ">=", fiscal_end),
        ], order="sequence, code, company_id desc, effective_from desc")
        selected = self.env["rebuild.account.declaration.rule"]
        for code in candidates.mapped("code"):
            matching = candidates.filtered(lambda rule: rule.code == code)
            company_rule = matching.filtered(
                lambda rule: rule.company_id == company,
            )[:1]
            rule = company_rule or matching.filtered(
                lambda item: not item.company_id,
            )[:1]
            if rule and rule.active and rule.lifecycle == "current":
                selected |= rule
        return selected.sorted(lambda rule: (rule.sequence, rule.code))

    @api.model
    def _rule_applies_to_profile(self, rule, company, period_start, period_end):
        if rule.effective_from > period_end or (rule.effective_to and rule.effective_to < period_end):
            return False
        if rule.corporate_tax_required and company.rebuild_corporate_tax_regime != "is":
            return False
        if rule.profit_tax_regime != "any" and rule.profit_tax_regime != company.rebuild_profit_tax_regime:
            return False
        if rule.vat_regime != "any" and rule.vat_regime != company.rebuild_vat_regime:
            return False
        if rule.code == "FR_2069_RCI" and not self._has_tax_credit_signal(company, fiscal_start=period_start, fiscal_end=period_end):
            return False
        first_start, first_end = company._rebuild_first_fiscalyear_dates()
        if (
            rule.trigger_kind == "not_first_fiscal_year"
            and first_start
            and first_end
            and period_start == first_start
            and period_end == first_end
        ):
            return False
        transition = fields.Date.to_date(company.rebuild_vat_transition_date)
        if rule.code in {"FR_3514", "FR_3517_S"} and transition and period_start >= transition:
            return False
        if rule.trigger_kind == "vat_transition":
            return bool(transition and period_start >= transition)
        if rule.trigger_kind == "dividend_transactions":
            return self._has_dividend_signal(company, period_start, period_end)
        if rule.trigger_kind == "eu_b2b_services":
            return self._has_eu_b2b_service_signal(company, period_start, period_end)
        if rule.trigger_kind == "oss_registration":
            return bool(company.rebuild_oss_registered)
        if rule.trigger_kind == "das2_threshold":
            return self._has_das2_signal(
                company,
                period_start,
                period_end,
                rule.threshold_amount or 2400.0,
            )
        if rule.trigger_kind == "cvae_turnover_threshold":
            return self._turnover(company, period_start, period_end) > (
                rule.threshold_amount or 152500.0
            )
        if rule.trigger_kind == "cvae_prior_liability":
            prior_start, prior_end = self._previous_closed_fiscal_period(
                company,
                period_start,
            )
            return (
                self._turnover(company, prior_start, prior_end)
                > (rule.threshold_amount or 500000.0)
                and self._external_value_exceeds(
                    company,
                    "1329-CVAE-PRIOR",
                    period_end,
                    rule.secondary_threshold_amount or 1500.0,
                )
            )
        if rule.trigger_kind == "company_creation":
            return bool(first_start and period_start <= first_start <= period_end)
        if rule.trigger_kind == "cfe_annual":
            return bool(first_start and period_end.year > first_start.year)
        if rule.trigger_kind == "cfe_prior_liability":
            return (
                not company.rebuild_cfe_monthly_payment
                and self._external_value_exceeds(
                    company,
                    "CFE-PRIOR",
                    period_end,
                    rule.threshold_amount or 3000.0,
                    inclusive=True,
                )
            )
        return True

    @api.model
    def _has_tax_credit_signal(self, company, fiscal_start, fiscal_end):
        return bool(self.env["rebuild.account.external.report.value"].search_count([
            ("company_id", "=", company.id),
            ("form_code", "ilike", "2069"),
            ("review_status", "!=", "superseded"),
        ]))

    @api.model
    def _has_dividend_signal(self, company, fiscal_start, fiscal_end):
        domain = [
            ("company_id", "=", company.id),
            ("move_id.state", "=", "posted"),
            ("account_id.code", "=like", "457%"),
            ("date", "<=", fiscal_end),
        ]
        if fiscal_start:
            domain.append(("date", ">=", fiscal_start))
        return bool(self.env["account.move.line"].with_company(company).search_count(domain))

    @api.model
    def _has_eu_b2b_service_signal(self, company, period_start, period_end):
        moves = self.env["account.move"].with_company(company).search([
            ("company_id", "=", company.id),
            ("state", "=", "posted"),
            ("move_type", "in", ["out_invoice", "out_refund"]),
            ("invoice_date", ">=", period_start),
            ("invoice_date", "<=", period_end),
            ("commercial_partner_id.country_id.code", "in", sorted(EU_COUNTRY_CODES - {"FR"})),
            ("commercial_partner_id.vat", "!=", False),
        ])
        markers = ("autoliquid", "intracom", "reverse charge")
        return any(
            any(
                any(marker in ((tax.name or "") + " " + (tax.description or "")).lower() for marker in markers)
                for tax in line.tax_ids
            )
            for move in moves
            for line in move.invoice_line_ids
        )

    @api.model
    def _has_das2_signal(self, company, period_start, period_end, threshold=2400.0):
        totals = self._das2_paid_totals(company, period_start, period_end)
        return any(total > threshold for total in totals.values())

    @api.model
    def _das2_paid_totals(self, company, period_start, period_end):
        """Return calendar-period payments to each DAS2 beneficiary.

        Direct bank/cash postings to 622 accounts and payments reconciled to
        posted supplier documents are counted. Merely posting an expense or
        accrual never creates the legal payment signal.
        """
        totals = {}
        lines = self.env["account.move.line"].with_company(company).search([
            ("company_id", "=", company.id),
            ("move_id.state", "=", "posted"),
            ("date", ">=", period_start),
            ("date", "<=", period_end),
            ("account_id.code", "=like", "622%"),
            ("partner_id", "!=", False),
            ("journal_id.type", "in", ["bank", "cash"]),
        ])
        for line in lines:
            partner = line.partner_id.commercial_partner_id
            totals[partner.id] = totals.get(partner.id, 0.0) + line.balance

        bills = self.env["account.move"].with_company(company).search([
            ("company_id", "=", company.id),
            ("state", "=", "posted"),
            ("move_type", "in", ["in_invoice", "in_refund"]),
            ("invoice_line_ids.account_id.code", "=like", "622%"),
        ])
        for bill in bills:
            eligible_balance = sum(
                bill.invoice_line_ids.filtered(
                    lambda line: (line.account_id.code or "").startswith("622"),
                ).mapped("balance"),
            )
            payable_lines = bill.line_ids.filtered(
                lambda line: line.account_id.account_type == "liability_payable",
            )
            payable_total = sum(abs(line.balance) for line in payable_lines)
            if not eligible_balance or not payable_total:
                continue
            eligible_ratio = min(abs(eligible_balance) / payable_total, 1.0)
            paid_amount = 0.0
            for payable_line in payable_lines:
                for partial in payable_line.matched_debit_ids | payable_line.matched_credit_ids:
                    payment_date = fields.Date.to_date(partial.max_date)
                    if not payment_date or not period_start <= payment_date <= period_end:
                        continue
                    other_line = (
                        partial.credit_move_id
                        if partial.debit_move_id == payable_line
                        else partial.debit_move_id
                    )
                    other_move = other_line.move_id
                    if not (
                        other_move.origin_payment_id
                        or other_move.statement_line_id
                        or other_move.journal_id.type in {"bank", "cash"}
                    ):
                        continue
                    paid_amount += partial.amount
            if paid_amount:
                partner = bill.commercial_partner_id
                sign = -1.0 if bill.move_type == "in_refund" else 1.0
                totals[partner.id] = totals.get(partner.id, 0.0) + (
                    sign * paid_amount * eligible_ratio
                )
        return totals

    @api.model
    def _turnover(self, company, period_start, period_end):
        lines = self.env["account.move.line"].with_company(company).search([
            ("company_id", "=", company.id),
            ("move_id.state", "=", "posted"),
            ("date", ">=", period_start),
            ("date", "<=", period_end),
            ("account_id.account_type", "in", ["income", "income_other"]),
        ])
        return sum(lines.mapped("credit")) - sum(lines.mapped("debit"))

    @api.model
    def _previous_closed_fiscal_period(self, company, as_of):
        anchor = fields.Date.to_date(as_of) - relativedelta(days=1)
        period_start, period_end = company.rebuild_compute_fiscalyear_dates(anchor)
        if period_end >= as_of:
            period_start, period_end = company.rebuild_compute_fiscalyear_dates(
                period_start - relativedelta(days=1),
            )
        return period_start, period_end

    @api.model
    def _external_value_exceeds(
        self,
        company,
        form_code,
        period_end,
        threshold,
        inclusive=False,
    ):
        values = self.env["rebuild.account.external.report.value"].search([
            ("company_id", "=", company.id),
            ("form_code", "=", form_code),
            ("review_status", "in", ["accepted", "accepted_with_difference"]),
        ])
        prior_year = str(period_end.year - 1)
        return any(
            prior_year in (value.period_key or "")
            and (
                (value.amount or 0.0) >= threshold
                if inclusive
                else (value.amount or 0.0) > threshold
            )
            for value in values
        )

    @api.model
    def _sync_rule_instances(self, company, rule, fiscal_start, fiscal_end):
        if rule.cadence == "is_instalments":
            deadlines = self._is_instalment_deadlines(fiscal_end)
            return self.browse().union([
                self._upsert_instance(company, rule, fiscal_start, fiscal_end, number, deadline, deadline)
                for number, deadline in enumerate(deadlines, start=1)
            ])
        if rule.cadence == "vat_instalments":
            windows = self._vat_instalment_windows(fiscal_start, fiscal_end)
            transition = fields.Date.to_date(company.rebuild_vat_transition_date)
            if transition:
                windows = [(start, end) for start, end in windows if end < transition]
            return self.browse().union([
                self._upsert_instance(company, rule, fiscal_start, fiscal_end, number, start, end)
                for number, (start, end) in enumerate(windows, start=1)
            ])
        if rule.code == "FR_3517_S":
            first_start, first_end = company._rebuild_first_fiscalyear_dates()
            if first_start == fiscal_start and first_end == fiscal_end and first_start.year < first_end.year:
                calendar_end = date(first_start.year, 12, 31)
                return self.browse().union((
                    self._upsert_instance(
                        company, rule, first_start, calendar_end, 1,
                        self._second_workday_after_may_first(calendar_end.year + 1),
                        self._second_workday_after_may_first(calendar_end.year + 1),
                        fiscal_start=fiscal_start, fiscal_end=fiscal_end,
                    ),
                    self._upsert_instance(
                        company, rule, date(first_end.year, 1, 1), first_end, 2,
                        _month_end(first_end + relativedelta(months=3)),
                        _month_end(first_end + relativedelta(months=3)),
                        fiscal_start=fiscal_start, fiscal_end=fiscal_end,
                    ),
                ))
        deadline = self._annual_deadline(rule.code, fiscal_end)
        return self._upsert_instance(company, rule, fiscal_start, fiscal_end, 0, deadline, deadline)

    @api.model
    def _sync_calendar_instances(self, company, horizon_start, horizon_end):
        declarations = self.browse()
        month = horizon_start.replace(day=1)
        while month <= horizon_end:
            month_end = _month_end(month)
            for rule in self._rules_for_period(company, month_end).filtered(lambda item: item.period_basis == "calendar_month"):
                if rule.code == "FR_3514":
                    if month.month not in {7, 12}:
                        continue
                    period_start = date(month.year, 1 if month.month == 7 else 7, 1)
                    period_end = (
                        date(month.year, 6, 30)
                        if month.month == 7
                        else date(month.year, 12, 31)
                    )
                    first_start, _first_end = company._rebuild_first_fiscalyear_dates()
                    if first_start and period_end < first_start:
                        continue
                    if first_start and period_start < first_start <= period_end:
                        period_start = first_start
                    deadline_start, deadline = date(month.year, month.month, 15), date(month.year, month.month, 24)
                    transition = fields.Date.to_date(company.rebuild_vat_transition_date)
                    if transition and deadline >= transition:
                        continue
                else:
                    period_start, period_end = month, month_end
                    if rule.deadline_rule == "des_tenth_workday":
                        deadline = self._nth_workday(month_end + timedelta(days=1), 10)
                    else:
                        deadline = (month_end + timedelta(days=1)).replace(day=15)
                    deadline_start = deadline
                if self._rule_applies_to_profile(rule, company, period_start, period_end):
                    declarations |= self._upsert_instance(company, rule, period_start, period_end, 0, deadline_start, deadline)
            month += relativedelta(months=1)

        for year in range(horizon_start.year, horizon_end.year + 1):
            year_start, year_end = date(year, 1, 1), date(year, 12, 31)
            for rule in self._rules_for_period(company, year_end).filtered(lambda item: item.period_basis in {"calendar_year", "event"}):
                if not self._rule_applies_to_profile(rule, company, year_start, year_end):
                    continue
                if rule.code == "FR_IFU_2561":
                    deadline = date(year + 1, 2, 15)
                elif rule.code == "FR_DAS2":
                    deadline = self._das2_deadline(company, year_end)
                elif rule.code == "FR_CFE_1447_C":
                    deadline = date(year, 12, 31)
                elif rule.code == "FR_CFE_BALANCE":
                    deadline = date(year, 12, 15)
                elif rule.code == "FR_CFE_ACOMPTE":
                    deadline = date(year, 6, 15)
                elif rule.code == "FR_CVAE_1329_AC":
                    for number, deadline in enumerate((date(year, 6, 15), date(year, 9, 15)), start=1):
                        declarations |= self._upsert_instance(
                            company, rule, year_start, year_end, number,
                            deadline, deadline,
                        )
                    continue
                else:
                    deadline = self._annual_deadline(rule.code, year_end)
                declarations |= self._upsert_instance(company, rule, year_start, year_end, 0, deadline, deadline)

            for quarter in range(1, 5):
                q_start = date(year, (quarter - 1) * 3 + 1, 1)
                q_end = _month_end(q_start + relativedelta(months=2))
                if q_end < horizon_start or q_end > horizon_end:
                    continue
                for rule in self._rules_for_period(company, q_end).filtered(lambda item: item.period_basis == "calendar_quarter"):
                    if self._rule_applies_to_profile(rule, company, q_start, q_end):
                        deadline = _month_end(q_end + relativedelta(months=1))
                        declarations |= self._upsert_instance(company, rule, q_start, q_end, quarter, deadline, deadline)
        return declarations

    @api.model
    def _retire_superseded_instances(self, company, current, horizon_start, horizon_end):
        stale = self.search([
            ("company_id", "=", company.id),
            ("period_end", ">=", horizon_start),
            ("period_start", "<=", horizon_end),
            ("id", "not in", current.ids),
            ("status", "not in", ["filed", "paid", "archived"]),
        ])
        if stale:
            stale.write({
                "applicability": "not_applicable",
                "status": "not_applicable",
                "validation_status": "ready",
                "preparation_status": "not_required",
                "applicability_reason": "Superseded by the governed period-aware French declaration schedule; retained for audit traceability.",
            })

    @api.model
    def _upsert_instance(self, company, rule, period_start, period_end, instalment_number, window_start, deadline, fiscal_start=None, fiscal_end=None):
        fiscal_start = fiscal_start or period_start
        fiscal_end = fiscal_end or period_end
        declaration = self.search([
            ("company_id", "=", company.id),
            ("rule_id", "=", rule.id),
            ("period_start", "=", period_start),
            ("period_end", "=", period_end),
            ("instalment_number", "=", instalment_number),
        ], limit=1)
        suffix = f" - instalment {instalment_number}" if instalment_number else ""
        period_label = (
            f"{fields.Date.to_string(period_start)} to {fields.Date.to_string(period_end)}"
            if rule.period_basis != "fiscal_year"
            else f"FY ending {fields.Date.to_string(fiscal_end)}"
        )
        vals = {
            "name": f"{rule.form_code}{suffix} - {period_label}",
            "company_id": company.id,
            "rule_id": rule.id,
            "period_start": period_start,
            "period_end": period_end,
            "fiscalyear_start": fiscal_start,
            "fiscalyear_end": fiscal_end,
            "instalment_number": instalment_number,
            "deadline_window_start": window_start,
            "deadline_date": deadline,
            "deadline_basis": self._deadline_basis(rule, fiscal_end, window_start, deadline),
            "applicability": "conditional" if rule.conditional else "applicable",
            "applicability_reason": self._applicability_reason(company, rule),
            "portal_entry_guidance": rule.filing_guidance,
        }
        if declaration:
            declaration.write(vals)
        else:
            declaration = self.create({
                **vals,
                "definition_snapshot": rule._definition_snapshot(),
            })
        return declaration

    @api.model
    def _applicability_reason(self, company, rule):
        profile = (
            f"{dict(company._fields['rebuild_legal_form'].selection).get(company.rebuild_legal_form, company.rebuild_legal_form)}, "
            f"{dict(company._fields['rebuild_corporate_tax_regime'].selection).get(company.rebuild_corporate_tax_regime, company.rebuild_corporate_tax_regime)}, "
            f"{dict(company._fields['rebuild_profit_tax_regime'].selection).get(company.rebuild_profit_tax_regime, company.rebuild_profit_tax_regime)}, "
            f"{dict(company._fields['rebuild_vat_regime'].selection).get(company.rebuild_vat_regime, company.rebuild_vat_regime)}"
        )
        return f"{rule.applicability_guidance}\nConfirmed company profile: {profile}."

    @api.model
    def _deadline_basis(self, rule, fiscal_end, window_start, deadline):
        if rule.cadence == "vat_instalments":
            return (
                f"{rule.deadline_guidance} The task uses the official 15-24 payment window; "
                f"the conservative displayed deadline is {fields.Date.to_string(deadline)}. "
                "Confirm the company's exact day in the professional tax portal."
            )
        if rule.code == "FR_DAS2":
            return (
                f"{rule.deadline_guidance} The payment year ends "
                f"{fields.Date.to_string(fiscal_end)} and this company's "
                f"governed filing deadline is {fields.Date.to_string(deadline)}."
            )
        return (
            f"{rule.deadline_guidance} Computed from the governed {rule.period_basis.replace('_', ' ')} "
            f"ending {fields.Date.to_string(fiscal_end)} under rule version {rule.version}."
        )

    @api.model
    def _annual_deadline(self, code, fiscal_end):
        if code in {"FR_2065", "FR_2033", "FR_2069_RCI", "FR_CVAE_1330"}:
            return _month_end(fiscal_end + relativedelta(months=3)) + relativedelta(days=15)
        if code == "FR_2572":
            return (fiscal_end + relativedelta(months=4)).replace(day=15)
        if code == "FR_3517_S":
            return _month_end(fiscal_end + relativedelta(months=3))
        return fiscal_end + relativedelta(months=3)

    @api.model
    def _das2_deadline(self, company, payment_year_end):
        _fiscal_start, fiscal_end = company.rebuild_compute_fiscalyear_dates(
            payment_year_end,
        )
        if fiscal_end == payment_year_end:
            return date(payment_year_end.year + 1, 4, 30)
        return fiscal_end + relativedelta(months=3)

    @api.model
    def _second_workday_after_may_first(self, year):
        return self._nth_workday(date(year, 5, 2), 2)

    @api.model
    def _nth_workday(self, start, count):
        day = start
        found = 0
        while True:
            if day.weekday() < 5:
                found += 1
                if found == count:
                    return day
            day += timedelta(days=1)

    @api.model
    def _is_instalment_deadlines(self, fiscal_end):
        marker = (fiscal_end.month, fiscal_end.day)
        year = fiscal_end.year
        if (2, 20) <= marker <= (5, 19):
            return [date(year - 1, 6, 15), date(year - 1, 9, 15), date(year - 1, 12, 15), date(year, 3, 15)]
        if (5, 20) <= marker <= (8, 19):
            return [date(year - 1, 9, 15), date(year - 1, 12, 15), date(year, 3, 15), date(year, 6, 15)]
        if (8, 20) <= marker <= (11, 19):
            return [date(year - 1, 12, 15), date(year, 3, 15), date(year, 6, 15), date(year, 9, 15)]
        return [date(year, 3, 15), date(year, 6, 15), date(year, 9, 15), date(year, 12, 15)]

    @api.model
    def _vat_instalment_windows(self, fiscal_start, fiscal_end):
        windows = []
        for year in range(fiscal_start.year, fiscal_end.year + 1):
            for month in (7, 12):
                start = date(year, month, 15)
                end = date(year, month, 24)
                if fiscal_start <= end <= fiscal_end:
                    windows.append((start, end))
        return windows
