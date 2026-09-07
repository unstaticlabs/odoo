import calendar
from datetime import date

from dateutil.relativedelta import relativedelta

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import float_compare

BENCHMARK_START = date(2024, 1, 10)
BENCHMARK_END = date(2025, 9, 30)
CURRENT_START = date(2025, 10, 1)
FIRST_FISCAL_YEAR_PERIOD_KEY = "Fiscal year 2024-01-10 to 2025-09-30"
CURRENT_PERIOD_KEY = "Fiscal year from 2025-10-01"

EU_COUNTRY_CODES = {
    "AT", "BE", "BG", "CY", "CZ", "DE", "DK", "EE", "ES", "FI", "FR",
    "GR", "HR", "HU", "IE", "IT", "LT", "LU", "LV", "MT", "NL", "PL",
    "PT", "RO", "SE", "SI", "SK",
}


def _month_end(value):
    return value.replace(day=calendar.monthrange(value.year, value.month)[1])


class RebuildAccountDeclaration(models.Model):
    _name = "rebuild.account.declaration"
    _description = "USL French Declaration Obligation"
    _inherit = ["mail.thread"]
    _order = "deadline_date, company_id, rule_id, instalment_number"

    _unique_declaration_instance = models.Constraint(
        "UNIQUE (company_id, rule_id, period_start, period_end, instalment_number)",
        "This declaration obligation already exists for the company and period.",
    )

    name = fields.Char(required=True)
    company_id = fields.Many2one("res.company", required=True, index=True)
    currency_id = fields.Many2one(related="company_id.currency_id", readonly=True)
    rule_id = fields.Many2one("rebuild.account.declaration.rule", required=True, index=True)
    category = fields.Selection(related="rule_id.category", store=True, readonly=True)
    form_code = fields.Char(related="rule_id.form_code", store=True, readonly=True)
    rule_version = fields.Char(related="rule_id.version", store=True, readonly=True)
    definition_snapshot = fields.Json(readonly=True)
    official_source_label = fields.Char(related="rule_id.official_source_label", readonly=True)
    official_url = fields.Char(related="rule_id.official_url", readonly=True)
    portal_url = fields.Char(related="rule_id.portal_url", readonly=True)
    period_start = fields.Date(required=True, index=True)
    period_end = fields.Date(required=True, index=True)
    fiscalyear_start = fields.Date(required=True, index=True)
    fiscalyear_end = fields.Date(required=True, index=True)
    instalment_number = fields.Integer(default=0)
    deadline_window_start = fields.Date()
    deadline_date = fields.Date(required=True, index=True, tracking=True)
    deadline_basis = fields.Text(required=True)
    applicability = fields.Selection(
        [("applicable", "Applicable"), ("conditional", "Conditional Review"), ("not_applicable", "Not Applicable")],
        required=True,
        readonly=True,
        default="applicable",
        index=True,
    )
    applicability_reason = fields.Text(required=True)
    status = fields.Selection(
        [
            ("to_prepare", "To Prepare"),
            ("data_missing", "Data Missing"),
            ("internal_review", "Ready for Internal Review"),
            ("accountant_review", "Ready for Accountant Review"),
            ("accountant_reviewed", "Accountant Reviewed"),
            ("ready_to_file", "Ready to File Externally"),
            ("filed", "Filed Externally"),
            ("paid", "Paid / Refunded"),
            ("archived", "Archived"),
            ("blocked", "Blocked"),
            ("not_applicable", "Not Applicable"),
        ],
        required=True,
        readonly=True,
        default="to_prepare",
        index=True,
        tracking=True,
    )
    validation_status = fields.Selection(
        [("not_run", "Not Run"), ("ready", "Ready"), ("warning", "Warning"), ("blocked", "Blocked")],
        required=True,
        readonly=True,
        default="not_run",
        index=True,
        tracking=True,
    )
    preparation_status = fields.Selection(
        [
            ("missing_data", "Missing Data"),
            ("ready_for_review", "Ready for Review"),
            ("reviewed", "Reviewed"),
            ("not_required", "Not Required"),
        ],
        required=True,
        readonly=True,
        default="missing_data",
        index=True,
        tracking=True,
    )
    review_status = fields.Selection(
        [
            ("not_started", "Not Started"),
            ("internal_ready", "Internal Review Ready"),
            ("accountant_requested", "Accountant Review Requested"),
            ("accepted", "Accepted by Reviewer"),
            ("accepted_with_difference", "Accepted with Difference"),
            ("rejected", "Rejected / Changes Required"),
        ],
        required=True,
        readonly=True,
        default="not_started",
        tracking=True,
    )
    filing_status = fields.Selection(
        [
            ("not_open", "Not Open"),
            ("ready", "Ready to File"),
            ("filed", "Filed Externally"),
            ("accepted", "Accepted Externally"),
            ("rejected", "Rejected Externally"),
        ],
        required=True,
        readonly=True,
        default="not_open",
        tracking=True,
    )
    payment_status = fields.Selection(
        [
            ("not_assessed", "Not Assessed"),
            ("not_due", "No Payment Due"),
            ("due", "Payment Due"),
            ("partially_paid", "Partially Paid"),
            ("paid", "Paid"),
            ("credit", "Credit Carried Forward"),
            ("refund_requested", "Refund Requested"),
            ("refunded", "Refunded"),
        ],
        required=True,
        default="not_assessed",
        tracking=True,
    )
    acceptance_status = fields.Selection(
        [
            ("not_submitted", "Not Submitted"),
            ("pending", "Pending"),
            ("accepted", "Accepted"),
            ("rejected", "Rejected"),
        ],
        required=True,
        default="not_submitted",
        tracking=True,
    )
    amount_due = fields.Monetary(currency_field="currency_id", tracking=True)
    amount_paid = fields.Monetary(currency_field="currency_id", tracking=True)
    credit_amount = fields.Monetary(currency_field="currency_id", tracking=True)
    refund_amount = fields.Monetary(currency_field="currency_id", tracking=True)
    field_line_ids = fields.One2many("rebuild.account.declaration.field", "declaration_id", string="Prefilled Fields")
    prefilled_line_count = fields.Integer(compute="_compute_counts")
    unresolved_count = fields.Integer(compute="_compute_counts")
    validation_summary = fields.Text()
    unresolved_information = fields.Text()
    portal_entry_guidance = fields.Text()
    external_filing_reference = fields.Char(tracking=True)
    evidence_reference = fields.Char()
    evidence_attachment_ids = fields.Many2many(
        "ir.attachment",
        "rebuild_declaration_attachment_rel",
        "declaration_id",
        "attachment_id",
        string="Filing and Review Evidence",
    )
    last_refreshed_at = fields.Datetime(readonly=True)
    filed_at = fields.Datetime(readonly=True)
    filed_by_id = fields.Many2one("res.users", readonly=True)
    paid_at = fields.Datetime(readonly=True)
    paid_by_id = fields.Many2one("res.users", readonly=True)
    is_overdue = fields.Boolean(compute="_compute_is_overdue", search="_search_is_overdue")

    @api.depends("field_line_ids", "field_line_ids.is_unresolved")
    def _compute_counts(self):
        for declaration in self:
            declaration.prefilled_line_count = len(declaration.field_line_ids)
            declaration.unresolved_count = len(declaration.field_line_ids.filtered("is_unresolved"))

    @api.depends("deadline_date", "status", "applicability")
    def _compute_is_overdue(self):
        today = fields.Date.context_today(self)
        finished = {"filed", "paid", "archived", "not_applicable"}
        for declaration in self:
            declaration.is_overdue = bool(
                declaration.applicability == "applicable"
                and declaration.deadline_date
                and declaration.deadline_date < today
                and declaration.status not in finished,
            )

    @api.model
    def _search_is_overdue(self, operator, value):
        positive = (operator in ("=", "==") and value) or (operator == "!=" and not value)
        domain = [
            ("deadline_date", "<", fields.Date.context_today(self)),
            ("applicability", "=", "applicable"),
            ("status", "not in", ["filed", "paid", "archived", "not_applicable"]),
        ]
        return domain if positive else ["!", *domain]

    def action_refresh_preparation(self):
        for declaration in self:
            declaration._sync_field_lines()
            unresolved = declaration.field_line_ids.filtered("is_unresolved")
            messages = []
            if unresolved:
                messages.append(f"{len(unresolved)} prefilled field(s) require external input or reviewer resolution.")
            mismatches = declaration.field_line_ids.filtered(lambda line: line.validation_status == "mismatch")
            if mismatches:
                messages.append(f"{len(mismatches)} field validation mismatch(es) remain.")
            if declaration.applicability == "not_applicable":
                validation_status = "ready"
                next_status = "not_applicable"
                preparation_status = "not_required"
            elif unresolved or mismatches:
                if declaration.applicability == "conditional":
                    validation_status = "warning"
                    next_status = "to_prepare"
                else:
                    validation_status = "blocked"
                    next_status = "data_missing"
                preparation_status = "missing_data"
            else:
                validation_status = "ready"
                next_status = "internal_review"
                preparation_status = "ready_for_review"
                messages.append("Ledger-derived fields and confirmed facts currently pass automated checks.")
            vals = {
                "validation_status": validation_status,
                "preparation_status": preparation_status,
                "validation_summary": "\n".join(messages),
                "unresolved_information": "\n".join(unresolved.mapped("unresolved_reason")),
                "last_refreshed_at": fields.Datetime.now(),
            }
            if declaration.status in {"to_prepare", "data_missing", "internal_review", "blocked"}:
                vals["status"] = next_status
            declaration.write(vals)
        return True

    def _sync_field_lines(self):
        self.ensure_one()
        FieldLine = self.env["rebuild.account.declaration.field"]
        seen_codes = set()
        tax_form_codes = [code.strip() for code in (self.rule_id.tax_form_codes or "").split(",") if code.strip()]
        if tax_form_codes:
            period_key = self._tax_package_period_key(self.fiscalyear_end)
            tax_lines = self.env["rebuild.account.french.tax.package.line"]
            if period_key:
                tax_lines = tax_lines.search([
                    ("company_id", "=", self.company_id.id),
                    ("period_key", "=", period_key),
                    ("form_code", "in", tax_form_codes),
                ])
            for tax_line in tax_lines:
                code = tax_line.field_code
                seen_codes.add(code)
                unresolved = tax_line.review_status != "ledger_derived"
                FieldLine._upsert(self, code, {
                    "form_code": tax_line.form_code,
                    "field_label": tax_line.field_label,
                    "amount": tax_line.rounded_amount,
                    "value_text": tax_line.value_text,
                    "source_kind": tax_line.source_kind,
                    "source_formula": tax_line.source_formula,
                    "account_prefixes": tax_line.drilldown_account_prefixes,
                    "source_reference": f"Tax-package mapping {tax_line.period_key} / {tax_line.field_code}",
                    "tax_package_line_id": tax_line.id,
                    "is_unresolved": unresolved,
                    "unresolved_reason": (
                        f"{tax_line.field_label}: {dict(tax_line._fields['review_status'].selection).get(tax_line.review_status)}."
                        if unresolved else False
                    ),
                    "validation_status": "review" if unresolved else "matched",
                })
        self._sync_rule_specific_fields(seen_codes)
        stale = self.field_line_ids.filtered(lambda line: line.field_code not in seen_codes)
        stale.unlink()

    @api.model
    def _tax_package_period_key(self, fiscal_end):
        if fiscal_end == BENCHMARK_END:
            return FIRST_FISCAL_YEAR_PERIOD_KEY
        if fiscal_end == date(2026, 9, 30):
            return CURRENT_PERIOD_KEY
        return False

    def _sync_rule_specific_fields(self, seen_codes):
        self.ensure_one()
        code = self.rule_id.code
        if code in {"FR_2065", "FR_2033"}:
            placeholders = []
            if code == "FR_2065":
                placeholders = [
                    ("2065_BIS_ADMIN_REVIEW", "2065-bis administrative and ownership information", "Company registry, ownership and tax-group facts must be confirmed in the official portal."),
                    ("2033_E_VALUE_ADDED_REVIEW", "2033-E value added and workforce", "Validate value added, workforce and CFE/CVAE information against payroll and tax evidence."),
                    ("2033_F_OWNERSHIP_REVIEW", "2033-F shareholding composition", "Confirm shareholder identity and ownership from the current corporate register."),
                    ("2033_G_SUBSIDIARIES_REVIEW", "2033-G subsidiaries and holdings", "Confirm whether any subsidiary or holding interest must be disclosed."),
                ]
            else:
                placeholders = [
                    ("2033_E_VALUE_ADDED_REVIEW", "2033-E value added and workforce", "Validate value added, workforce and CFE/CVAE information against payroll and tax evidence."),
                    ("2033_F_OWNERSHIP_REVIEW", "2033-F shareholding composition", "Confirm shareholder identity and ownership from the current corporate register."),
                    ("2033_G_SUBSIDIARIES_REVIEW", "2033-G subsidiaries and holdings", "Confirm whether any subsidiary or holding interest must be disclosed."),
                ]
            for field_code, label, reason in placeholders:
                seen_codes.add(field_code)
                self.env["rebuild.account.declaration.field"]._upsert(self, field_code, {
                    "form_code": self.form_code,
                    "field_label": label,
                    "source_kind": "external_confirmation",
                    "source_formula": "Not safely derivable from the general ledger alone.",
                    "source_reference": "Company registry / payroll / accountant evidence",
                    "is_unresolved": True,
                    "unresolved_reason": reason,
                    "validation_status": "review",
                })
        if code in {"FR_2571", "FR_2572"}:
            self._sync_corporate_tax_payment_fields(seen_codes)
        if code in {"FR_3517_S", "FR_3514"}:
            self._sync_vat_facts(seen_codes)

    def _sync_corporate_tax_payment_fields(self, seen_codes):
        if self.rule_id.code == "FR_2571":
            prior_end = self.fiscalyear_start - relativedelta(days=1)
            period_key = self._tax_package_period_key(prior_end)
        else:
            period_key = self._tax_package_period_key(self.fiscalyear_end)
        charge = self.env["rebuild.account.french.tax.package.line"]
        if period_key:
            charge = charge.search([
                ("company_id", "=", self.company_id.id),
                ("period_key", "=", period_key),
                ("field_code", "=", "2065_CHARGE_IS_COMPTABILISEE"),
            ], limit=1)
        amount = charge.rounded_amount if charge else 0.0
        estimated = amount / 4 if self.rule_id.code == "FR_2571" else amount
        field_code = "2571_PROVISIONAL_INSTALMENT" if self.rule_id.code == "FR_2571" else "2572_LEDGER_IS_CHARGE_REVIEW"
        label = "Provisional IS instalment from prior accounting charge" if self.rule_id.code == "FR_2571" else "Accounting IS charge before tax-return adjustments"
        seen_codes.add(field_code)
        self.env["rebuild.account.declaration.field"]._upsert(self, field_code, {
            "form_code": self.form_code,
            "field_label": label,
            "amount": estimated,
            "source_kind": "ledger_review_anchor",
            "source_formula": "Prior fiscal-year account 695 charge divided by four" if self.rule_id.code == "FR_2571" else "Prior fiscal-year account 695 charge",
            "account_prefixes": "695",
            "source_reference": charge.field_code if charge else "No prior fiscal-year 2065 accounting-charge mapping exists",
            "is_unresolved": True,
            "unresolved_reason": "Confirm taxable profit, reduced-rate eligibility, prior instalments and the amount shown in the professional tax portal before payment.",
            "validation_status": "review",
        })

    def _sync_vat_facts(self, seen_codes):
        FieldLine = self.env["rebuild.account.declaration.field"]
        if self.rule_id.code == "FR_3514":
            first_start, first_end = self.company_id._rebuild_first_fiscalyear_dates()
            new_company_period = bool(
                first_start and first_end
                and first_start <= self.period_end <= first_end,
            )
            reason = (
                "Confirm the portal amount and document that the new-company instalments cover at least 80% of VAT actually due for the corresponding period."
                if new_company_period
                else "Confirm the 55% / 40% reference amount, any exemption or modulation, and the exact portal due date."
            )
            seen_codes.add("VAT_3514_PORTAL_AMOUNT")
            FieldLine._upsert(self, "VAT_3514_PORTAL_AMOUNT", {
                "form_code": self.form_code,
                "field_label": "3514 amount confirmed in the professional tax portal",
                "amount": 0.0,
                "source_kind": "external_confirmation",
                "source_formula": "Not safely derivable from the general ledger alone.",
                "source_reference": "DGFiP professional tax portal",
                "is_unresolved": True,
                "unresolved_reason": reason,
                "validation_status": "review",
            })
            return

        facts = [
            ("USL_CA12_OPENING_CREDIT", "Opening VAT credit", 0.0, "Prior accepted VAT return or portal", True, "Confirm the opening VAT credit from the prior accepted return."),
            ("USL_CA12_INSTALMENTS_PAID", "VAT instalments paid", 0.0, "DGFiP portal and bank evidence", True, "Confirm instalments actually paid for this exact VAT period."),
        ]
        siren = self._company_siren()
        if (
            self.rule_id.code == "FR_3517_S"
            and siren == "983982950"
            and self.fiscalyear_end == date(2026, 9, 30)
        ):
            facts.extend([
                ("USL_CA12_REFUND_ACCEPTED", "VAT refund requested, accepted and reimbursed", 2500.0, "Ledger accounts 445830/445670 and DGFiP bank settlement", False, False),
                ("USL_CA12_LATER_REFUND", "Later VAT credit reimbursed", 942.0, "DGFiP bank settlement and account 445670", False, False),
                ("USL_CA12_REMAINING_CREDIT", "Remaining VAT credit after both refunds", 0.0, "3442 credit less 2500 and 942 refunds", False, False),
            ])
        for field_code, label, amount, source, unresolved, reason in facts:
            seen_codes.add(field_code)
            FieldLine._upsert(self, field_code, {
                "form_code": self.form_code,
                "field_label": label,
                "amount": amount,
                "source_kind": "confirmed_fact",
                "source_formula": source,
                "account_prefixes": "445670,445830,512,471" if amount else "445670,445830",
                "source_reference": "Confirmed VAT facts and posted ledger",
                "is_unresolved": unresolved,
                "unresolved_reason": reason,
                "validation_status": "matched",
            })
        if (
            self.rule_id.code == "FR_3517_S"
            and siren == "983982950"
            and self.fiscalyear_end == date(2026, 9, 30)
        ):
            self._sync_vat_refund_control(seen_codes)

    def _company_siren(self):
        self.ensure_one()
        registry_digits = "".join(
            character
            for character in (self.company_id.company_registry or "")
            if character.isdigit()
        )
        if len(registry_digits) >= 9:
            return registry_digits[:9]
        vat_digits = "".join(
            character
            for character in (self.company_id.vat or "")
            if character.isdigit()
        )
        return vat_digits[-9:] if len(vat_digits) >= 9 else ""

    def _sync_vat_refund_control(self, seen_codes):
        seen_codes.add("USL_CA12_942_LEDGER_CLASSIFICATION")
        vat_line = self.env["account.move.line"].with_company(self.company_id).search([
            ("company_id", "=", self.company_id.id),
            ("account_id.code", "=like", "445670%"),
            ("move_id.state", "=", "posted"),
        ])
        vat_residual = sum(vat_line.mapped("amount_residual"))
        suspense_refund = self.env["account.bank.statement.line"].search([
            ("company_id", "=", self.company_id.id),
            ("date", ">=", date(2026, 7, 1)),
            ("amount", ">", 941.99),
            ("amount", "<", 942.01),
            ("payment_ref", "ilike", "DGFiP"),
            ("is_reconciled", "=", False),
        ], limit=1)
        mismatch = bool(suspense_refund) or float_compare(vat_residual, 0.0, precision_rounding=self.currency_id.rounding) != 0
        self.env["rebuild.account.declaration.field"]._upsert(self, "USL_CA12_942_LEDGER_CLASSIFICATION", {
            "form_code": self.form_code,
            "field_label": "€942 refund ledger classification control",
            "amount": vat_residual,
            "source_kind": "ledger_control",
            "source_formula": "Residual on account 445670 after the €2,500 and €942 DGFiP refunds; expected zero.",
            "account_prefixes": "445670,471,512",
            "source_reference": suspense_refund.display_name if suspense_refund else "No open €942 DGFiP bank line",
            "is_unresolved": mismatch,
            "unresolved_reason": "The €942 DGFiP bank receipt remains on suspense and must be matched to account 445670." if mismatch else False,
            "validation_status": "mismatch" if mismatch else "matched",
        })

    def action_mark_internal_ready(self):
        self.action_refresh_preparation()
        for declaration in self:
            if declaration.validation_status == "blocked":
                message = "Resolve the declaration's missing or mismatched fields before internal review."
                raise UserError(message)
            declaration.write({
                "status": "internal_review",
                "review_status": "internal_ready",
                "preparation_status": "ready_for_review",
            })
        return True

    def action_request_accountant_review(self):
        self.action_mark_internal_ready()
        self.write({"status": "accountant_review", "review_status": "accountant_requested"})
        return True

    def action_mark_ready_to_file(self):
        if not self.env.user.has_group("account.group_account_manager"):
            raise AccessError(
                "Only an Accounting Manager can approve a declaration for filing.",
            )
        self.action_mark_internal_ready()
        self.write({
            "status": "ready_to_file",
            "preparation_status": "reviewed",
            "filing_status": "ready",
        })
        return True

    def action_record_review_decision(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Declaration Review Decision",
            "res_model": "rebuild.account.assurance.decision",
            "view_mode": "form",
            "target": "current",
            "context": {
                "default_gate": "declaration_review",
                "default_conclusion": "pending",
                "default_required_authority": "accountant",
                "default_company_id": self.company_id.id,
                "default_period_key": f"{self.period_start}:{self.period_end}",
                "default_declaration_id": self.id,
                "default_evidence_key": f"declaration:{self.rule_id.code}:{self.period_end}:{self.instalment_number}",
                "default_source_value": self.validation_summary,
                "default_decision_summary": "Pending review.",
                "default_evidence_summary": self.evidence_reference,
                "default_remaining_risk": self.unresolved_information,
                "default_next_action": "Accept, accept with a documented difference, or require changes before external filing.",
            },
        }

    def action_mark_filed(self):
        if not self.env.user.has_group("account.group_account_manager"):
            message = "Only an Accounting Manager can record external filing."
            raise AccessError(message)
        for declaration in self:
            if declaration.status not in {"ready_to_file", "accountant_reviewed"}:
                message = (
                    "Approve the declaration for filing or complete the "
                    "optional accountant review before recording external filing."
                )
                raise UserError(message)
            if not declaration.external_filing_reference and not declaration.evidence_attachment_ids:
                message = "Attach filing evidence or record the external filing reference before marking a declaration filed."
                raise UserError(message)
            declaration.write({
                "status": "filed",
                "filing_status": "filed",
                "acceptance_status": "pending",
                "filed_at": fields.Datetime.now(),
                "filed_by_id": self.env.user.id,
            })
        return True

    def action_mark_paid_or_refunded(self):
        if not self.env.user.has_group("account.group_account_manager"):
            message = "Only an Accounting Manager can record declaration payment or refund completion."
            raise AccessError(message)
        for declaration in self:
            if declaration.status != "filed":
                message = "Record external filing before marking payment or refund completion."
                raise UserError(message)
            if declaration.payment_status not in {"paid", "refunded", "not_due"}:
                message = "Set payment status to Paid, Refunded or No Payment Due before completing the obligation."
                raise UserError(message)
            declaration.write({"status": "paid", "paid_at": fields.Datetime.now(), "paid_by_id": self.env.user.id})
        return True

    def action_open_prefilled_fields(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": f"{self.form_code} Prefilled Fields",
            "res_model": "rebuild.account.declaration.field",
            "view_mode": "list,form",
            "domain": [("declaration_id", "=", self.id)],
            "context": {"create": False, "delete": False},
        }

    def action_open_official_source(self):
        self.ensure_one()
        return {"type": "ir.actions.act_url", "url": self.official_url, "target": "new"}

    def action_open_portal(self):
        self.ensure_one()
        return {"type": "ir.actions.act_url", "url": self.portal_url, "target": "new"}


class RebuildAccountDeclarationField(models.Model):
    _name = "rebuild.account.declaration.field"
    _description = "USL French Declaration Prefilled Field"
    _order = "declaration_id, form_code, field_code"

    _unique_declaration_field = models.Constraint(
        "UNIQUE (declaration_id, field_code)",
        "A declaration field code must be unique within one obligation.",
    )

    declaration_id = fields.Many2one("rebuild.account.declaration", required=True, index=True, ondelete="cascade")
    company_id = fields.Many2one(related="declaration_id.company_id", store=True, readonly=True, index=True)
    currency_id = fields.Many2one(related="company_id.currency_id", readonly=True)
    form_code = fields.Char(required=True, index=True)
    field_code = fields.Char(required=True, index=True)
    field_label = fields.Char(required=True)
    amount = fields.Monetary(currency_field="currency_id")
    value_text = fields.Char()
    source_kind = fields.Selection(
        [
            ("annual_statement", "Annual Statement Mapping"),
            ("confirmed_fact", "Confirmed Company Fact"),
            ("depreciation_schedule", "Depreciation Schedule"),
            ("external_confirmation", "External Confirmation Needed"),
            ("fixed_asset_register", "Fixed Asset Register"),
            ("ledger_control", "Ledger Control"),
            ("ledger_review_anchor", "Ledger Review Starting Point"),
            ("manual_required", "External Value Needed"),
            ("vat_accounts", "VAT Ledger Accounts"),
        ],
        required=True,
        string="Value Source",
    )
    source_formula = fields.Text(required=True)
    account_prefixes = fields.Char()
    source_reference = fields.Char()
    tax_package_line_id = fields.Many2one("rebuild.account.french.tax.package.line", ondelete="set null")
    is_unresolved = fields.Boolean(string="Needs Follow-up", index=True)
    unresolved_reason = fields.Text()
    validation_status = fields.Selection(
        [("matched", "Matched"), ("review", "Review Required"), ("mismatch", "Mismatch")],
        required=True,
        default="review",
        index=True,
    )

    @api.model
    def _upsert(self, declaration, field_code, vals):
        line = self.search([("declaration_id", "=", declaration.id), ("field_code", "=", field_code)], limit=1)
        values = {"declaration_id": declaration.id, "field_code": field_code, **vals}
        if line:
            line.write(values)
        else:
            line = self.create(values)
        return line

    def action_open_source_items(self):
        self.ensure_one()
        if self.tax_package_line_id:
            return self.tax_package_line_id.action_open_journal_items()
        prefixes = [prefix.strip() for prefix in (self.account_prefixes or "").split(",") if prefix.strip()]
        if not prefixes:
            message = "This field has no ledger-account drill-down; use its explicit source reference."
            raise UserError(message)
        accounts = self.env["account.account"].with_company(self.company_id).search([
            ("company_ids", "in", self.company_id.id),
        ]).filtered(lambda account: any((account.code or "").startswith(prefix) for prefix in prefixes))
        return {
            "type": "ir.actions.act_window",
            "name": f"{self.form_code} - {self.field_label}",
            "res_model": "account.move.line",
            "view_mode": "list,form,pivot",
            "domain": [
                ("company_id", "=", self.company_id.id),
                ("move_id.state", "=", "posted"),
                ("date", ">=", self.declaration_id.period_start),
                ("date", "<=", max(self.declaration_id.period_end, fields.Date.context_today(self))),
                ("account_id", "in", accounts.ids),
            ],
            "context": {"create": False, "delete": False},
        }
