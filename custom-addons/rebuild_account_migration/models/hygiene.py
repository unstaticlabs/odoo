import json

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import date_utils


class RebuildAccountHygieneIssue(models.Model):
    _name = "rebuild.account.hygiene.issue"
    _description = "Accounting Hygiene Issue"
    _rec_name = "title"
    _order = "severity, issue_date desc, id desc"

    _unique_hygiene_issue = models.Constraint(
        "UNIQUE (company_id, issue_key)",
        "An Accounting Hygiene issue key must be unique within one company.",
    )

    issue_key = fields.Char(required=True, index=True, readonly=True)
    definition_id = fields.Many2one(
        "rebuild.account.closing.control.definition",
        string="Accounting Control",
        index=True,
        ondelete="restrict",
        readonly=True,
    )
    definition_version = fields.Char(readonly=True)
    definition_snapshot = fields.Json(readonly=True)
    control_code = fields.Char(index=True, readonly=True)
    company_id = fields.Many2one(
        "res.company",
        required=True,
        index=True,
        default=lambda self: self.env.company,
    )
    currency_id = fields.Many2one(related="company_id.currency_id", readonly=True)
    issue_type = fields.Selection(
        [
            ("reconciliation", "Reconciliation"),
            ("draft", "Draft Preparation"),
            ("evidence", "Missing Evidence"),
            ("analytic", "Analytic Allocation"),
            ("duplicate", "Possible Duplicate"),
            ("technical", "Technical Failure"),
        ],
        required=True,
        index=True,
        string="Area",
    )
    severity = fields.Selection(
        [
            ("1_blocking", "Blocking"),
            ("2_warning", "Warning"),
            ("3_attention", "Attention"),
            ("4_information", "Information"),
        ],
        required=True,
        index=True,
    )
    status = fields.Selection(
        [
            ("open", "Open"),
            ("resolved", "Resolved"),
            ("dismissed", "Dismissed"),
        ],
        required=True,
        default="open",
        index=True,
    )
    title = fields.Char(required=True)
    description = fields.Text(required=True)
    why_it_matters = fields.Text(required=True)
    recommended_action = fields.Text(required=True)
    accounting_consequence = fields.Text(required=True)
    evidence = fields.Text(required=True)
    confidence = fields.Selection(
        [
            ("high", "High — deterministic control"),
            ("medium", "Medium — review required"),
        ],
        required=True,
        default="high",
    )
    result_kind = fields.Selection(
        [
            ("accounting", "Accounting Result"),
            ("technical", "Technical Failure"),
        ],
        required=True,
        default="accounting",
        index=True,
        readonly=True,
        string="Result Type",
    )
    owner_role = fields.Selection(
        [
            ("accounting_manager", "Accounting Manager"),
            ("accountant_reviewer", "Accountant Reviewer"),
            ("finance_operator", "Finance Operator / Agent"),
        ],
        required=True,
        default="accounting_manager",
        string="Responsible Role",
    )
    owner_user_id = fields.Many2one(
        "res.users",
        string="Assigned User",
        domain="[('company_ids', 'in', [company_id])]",
    )
    issue_date = fields.Date(index=True, string="Detected On")
    amount = fields.Monetary(
        currency_field="currency_id",
        string="Amount Affected",
    )
    target_model = fields.Char(readonly=True)
    target_res_id = fields.Integer(readonly=True)
    target_res_ids_json = fields.Text(readonly=True)
    source_label = fields.Char(readonly=True, string="Related Record")
    first_detected_at = fields.Datetime(
        required=True,
        default=fields.Datetime.now,
        readonly=True,
        string="First Detected",
    )
    last_detected_at = fields.Datetime(
        required=True,
        default=fields.Datetime.now,
        readonly=True,
        string="Last Checked",
    )
    resolved_at = fields.Datetime(readonly=True)
    dismissed_at = fields.Datetime(readonly=True)
    dismissed_by_id = fields.Many2one("res.users", readonly=True)

    @staticmethod
    def _counted(count, singular, plural=None):
        return f"{count} {singular if count == 1 else (plural or singular + 's')}"

    @api.model
    def _issue_values(
        self,
        company,
        issue_key,
        control_code,
        issue_type,
        severity,
        title,
        description,
        why_it_matters,
        recommended_action,
        accounting_consequence,
        evidence,
        record,
        *,
        amount=0.0,
        confidence="high",
        owner_role="accounting_manager",
        issue_date=None,
        target_res_ids=None,
    ):
        return {
            "issue_key": issue_key,
            "control_code": control_code,
            "company_id": company.id,
            "issue_type": issue_type,
            "severity": severity,
            "title": title,
            "description": description,
            "why_it_matters": why_it_matters,
            "recommended_action": recommended_action,
            "accounting_consequence": accounting_consequence,
            "evidence": evidence,
            "confidence": confidence,
            "owner_role": owner_role,
            "issue_date": issue_date,
            "amount": abs(amount),
            "target_model": record._name,
            "target_res_id": record.id,
            "target_res_ids_json": json.dumps(target_res_ids or [record.id]),
            "source_label": record.display_name,
            "last_detected_at": fields.Datetime.now(),
        }

    @api.model
    def _evaluate_builtin_hygiene(self, company):
        now = fields.Date.context_today(self)
        cutoff = date_utils.subtract(now, days=30)
        candidates = []

        bank_lines = self.env["account.bank.statement.line"].search([
            ("company_id", "=", company.id),
            ("is_reconciled", "=", False),
        ])
        if bank_lines:
            line = bank_lines.sorted(lambda item: (item.date or now, item.id))[0]
            residual = sum(abs(value) for value in bank_lines.mapped("amount_residual"))
            bank_transaction_label = self._counted(
                len(bank_lines),
                "bank transaction",
            )
            bank_line_label = self._counted(len(bank_lines), "bank line")
            candidates.append(self._issue_values(
                company,
                "bank:unmatched",
                "hygiene_bank_unmatched",
                "reconciliation",
                "2_warning",
                f"Match {bank_transaction_label}",
                "These bank transactions still have no complete accounting counterpart.",
                "Until they are matched, cash movements and their business purpose remain disconnected.",
                "Open the affected transactions, then use Bank Matching with the closest amount and date suggestions to match or categorize each one.",
                "Cash may be correct while counterpart accounts, partners, taxes or open-item statuses remain incomplete.",
                f"{bank_line_label} {'is' if len(bank_lines) == 1 else 'are'} included in this result.",
                line,
                amount=residual,
                owner_role="finance_operator",
                issue_date=max(bank_lines.mapped("date")),
                target_res_ids=bank_lines.ids,
            ))

        documents = self.env["account.move"].search([
            ("company_id", "=", company.id),
            ("move_type", "in", ["out_invoice", "out_refund", "in_invoice", "in_refund", "out_receipt", "in_receipt"]),
            ("state", "!=", "cancel"),
        ])
        missing_vendor_evidence = documents.filtered(
            lambda item: not item.message_main_attachment_id
            and item.move_type in {"in_invoice", "in_refund", "in_receipt"}
            and not item.rebuild_source_id
        )
        if missing_vendor_evidence:
            move = missing_vendor_evidence.sorted("id")[0]
            supplier_document_label = self._counted(
                len(missing_vendor_evidence),
                "supplier document",
            )
            total = sum(
                abs(value)
                for value in missing_vendor_evidence.mapped("amount_total_signed")
            )
            candidates.append(self._issue_values(
                company,
                "vendor-evidence:missing",
                "hygiene_vendor_evidence",
                "evidence",
                "2_warning",
                f"Add evidence to {supplier_document_label}",
                "These new supplier documents have no main invoice, receipt or supporting file attached.",
                "The accounting entries can be correct but remain difficult to review or substantiate.",
                "Open the affected documents and attach each supplier invoice, receipt or a note explaining why evidence is unavailable.",
                "Deductibility, VAT support and the audit trail may be challenged without source evidence.",
                f"{supplier_document_label.capitalize()} without imported source evidence {'is' if len(missing_vendor_evidence) == 1 else 'are'} included. Imported source documents are excluded because historical attachments were deliberately outside dump parity.",
                move,
                amount=total,
                owner_role="finance_operator",
                issue_date=max(
                    (item.invoice_date or item.date)
                    for item in missing_vendor_evidence
                ),
                target_res_ids=missing_vendor_evidence.ids,
            ))
        stale_documents = documents.filtered(
            lambda item: item.state == "draft" and item.date and item.date < cutoff
        )
        if stale_documents:
            move = stale_documents.sorted("id")[0]
            stale_document_label = self._counted(
                len(stale_documents),
                "stale draft document",
            )
            total = sum(
                abs(value)
                for value in stale_documents.mapped("amount_total_signed")
            )
            candidates.append(self._issue_values(
                company,
                "stale-draft:documents",
                "hygiene_stale_documents",
                "draft",
                "3_attention",
                f"Finish or discard {stale_document_label}",
                "These draft business documents have remained unposted for more than 30 days.",
                "Old drafts are easily forgotten and may hide missing liabilities, receivables or corrections.",
                "Open the affected drafts, correct their business fields and taxes, then post or cancel each one with a documented reason.",
                "The ledger and reports exclude drafts unless explicitly requested, so the period may appear incomplete.",
                f"{stale_document_label.capitalize()} older than 30 days {'is' if len(stale_documents) == 1 else 'are'} included in this result.",
                move,
                amount=total,
                owner_role="finance_operator",
                issue_date=max(stale_documents.mapped("date")),
                target_res_ids=stale_documents.ids,
            ))

        expenses = self.env["hr.expense"].search([
            ("company_id", "=", company.id),
            ("state", "!=", "refused"),
        ])
        missing_expense_evidence = expenses.filtered(
            lambda item: not item.message_main_attachment_id
            and not item.rebuild_source_id
        )
        if missing_expense_evidence:
            expense = missing_expense_evidence.sorted("id")[0]
            expense_label = self._counted(
                len(missing_expense_evidence),
                "expense",
            )
            total = sum(
                abs(value)
                for value in missing_expense_evidence.mapped("total_amount")
            )
            candidates.append(self._issue_values(
                company,
                "expense-evidence:missing",
                "hygiene_expense_evidence",
                "evidence",
                "2_warning",
                f"Add receipts to {expense_label}",
                "These new expenses have no receipt or supporting file attached.",
                "Receipts are needed to review business purpose, tax and reimbursable amounts.",
                "Open the affected expenses and attach each receipt, or document why evidence cannot be obtained.",
                "Expenses and deductible VAT may be unsupported.",
                f"{expense_label.capitalize()} without imported source evidence {'is' if len(missing_expense_evidence) == 1 else 'are'} included. Imported expenses are excluded because historical attachments were deliberately outside dump parity.",
                expense,
                amount=total,
                owner_role="finance_operator",
                issue_date=max(missing_expense_evidence.mapped("date")),
                target_res_ids=missing_expense_evidence.ids,
            ))
        stale_expenses = expenses.filtered(
            lambda item: item.state in {"draft", "submitted", "approved"}
            and item.date
            and item.date < cutoff
        )
        if stale_expenses:
            expense = stale_expenses.sorted("id")[0]
            stale_expense_label = self._counted(
                len(stale_expenses),
                "stale expense workflow",
            )
            total = sum(
                abs(value) for value in stale_expenses.mapped("total_amount")
            )
            candidates.append(self._issue_values(
                company,
                "stale-expense:workflow",
                "hygiene_stale_expenses",
                "draft",
                "3_attention",
                f"Continue {stale_expense_label}",
                "These expense workflows have not reached accounting for more than 30 days.",
                "Unfinished expenses can delay reimbursement and omit costs from management reporting.",
                "Open the affected expenses, complete their fields and evidence, then submit, approve or refuse them.",
                "Expense, VAT, payable and analytic reporting may remain incomplete.",
                f"{stale_expense_label.capitalize()} older than 30 days {'is' if len(stale_expenses) == 1 else 'are'} included in this result.",
                expense,
                amount=total,
                owner_role="finance_operator",
                issue_date=max(stale_expenses.mapped("date")),
                target_res_ids=stale_expenses.ids,
            ))

        analytic_lines = self.env["account.move.line"].search([
            ("company_id", "=", company.id),
            ("parent_state", "=", "posted"),
            ("account_id.account_type", "in", [
                "income", "income_other", "expense", "expense_other",
                "expense_depreciation", "expense_direct_cost",
            ]),
            ("balance", "!=", 0),
            ("analytic_distribution", "=", False),
        ])
        if analytic_lines:
            line = analytic_lines.sorted("id")[0]
            journal_item_label = self._counted(
                len(analytic_lines),
                "journal item",
            )
            total = sum(abs(value) for value in analytic_lines.mapped("balance"))
            candidates.append(self._issue_values(
                company,
                "analytic:unallocated",
                "hygiene_analytic_allocation",
                "analytic",
                "3_attention",
                f"Review analytic allocation on {journal_item_label}",
                "These posted profit-and-loss lines have no analytic distribution.",
                "General accounting remains balanced, but activity and brand reporting is incomplete.",
                "Open the affected journal items or source documents and add the appropriate analytic allocation where the line is in scope.",
                "Revenue vs Spending and analytic profitability omit these amounts from configured dimensions.",
                f"{journal_item_label.capitalize()} {'is' if len(analytic_lines) == 1 else 'are'} included in this result.",
                line,
                amount=total,
                owner_role="finance_operator",
                issue_date=max(analytic_lines.mapped("date")),
                target_res_ids=analytic_lines.ids,
            ))

        duplicate_groups = {}
        for move in documents.filtered(
            lambda item: item.state == "posted"
            and item.move_type in {"in_invoice", "in_refund"}
            and item.partner_id
            and (item.ref or item.payment_reference)
        ):
            key = (
                move.move_type,
                move.partner_id.id,
                (move.ref or move.payment_reference or "").strip().casefold(),
                round(move.amount_total_signed, 2),
                move.invoice_date,
            )
            duplicate_groups.setdefault(key, self.env["account.move"])
            duplicate_groups[key] |= move
        for group in duplicate_groups.values():
            if len(group) < 2:
                continue
            first = group.sorted("id")[0]
            candidates.append(self._issue_values(
                company,
                "duplicate:" + "-".join(str(record_id) for record_id in sorted(group.ids)),
                "hygiene_duplicate_documents",
                "duplicate",
                "2_warning",
                f"Review {len(group)} possible duplicate supplier documents",
                "These posted supplier documents share the same partner, reference, date and signed total.",
                "The match is deterministic, but repeated invoices can also be legitimate and require human review.",
                "Open the documents, compare their source evidence and payments, then reverse only a confirmed duplicate.",
                "A true duplicate overstates expense, VAT and supplier liability.",
                "; ".join(group.mapped("display_name")),
                first,
                amount=sum(abs(value) for value in group.mapped("amount_total_signed")),
                confidence="medium",
                owner_role="accounting_manager",
                issue_date=first.invoice_date or first.date,
                target_res_ids=group.ids,
            ))
        return candidates

    @api.model
    def _hygiene_evaluator_registry(self):
        """Whitelisted evaluator extension point for installed modules."""
        return {"builtin_hygiene": "_evaluate_builtin_hygiene"}

    @api.model
    def _candidate_values(self, company):
        Definition = self.env["rebuild.account.closing.control.definition"]
        definitions = Definition._ensure_for_company(company).filtered(
            lambda definition: (
                definition.enabled
                and definition.applies_to_hygiene
                and definition._is_effective(
                    fields.Date.context_today(self),
                )
            ),
        )
        definitions_by_code = {
            definition.code: definition for definition in definitions
        }
        evaluator_registry = self._hygiene_evaluator_registry()
        candidates = []
        for evaluator_key in set(definitions.mapped("evaluator_key")):
            evaluator_name = evaluator_registry.get(evaluator_key)
            evaluator_definitions = definitions.filtered(
                lambda definition: definition.evaluator_key == evaluator_key,
            )
            try:
                evaluated = self._run_hygiene_evaluator(
                    evaluator_key,
                    evaluator_name,
                    company,
                )
            except Exception as exc:  # noqa: BLE001 - persist governed technical results.
                for definition in evaluator_definitions:
                    values = self._issue_values(
                        company,
                        f"technical:{definition.code}",
                        definition.code,
                        "technical",
                        "1_blocking",
                        f"{definition.name} could not run",
                        "The configured control evaluator failed before it could determine whether an accounting issue exists.",
                        "A technical failure is not an accounting failure, but readiness cannot rely on a control that did not run.",
                        "Ask a Technical Administrator to inspect the evaluator and retry the control.",
                        "No accounting conclusion was produced.",
                        f"{type(exc).__name__}: {exc}",
                        definition,
                        owner_role="accounting_manager",
                    )
                    candidates.append({
                        **values,
                        "definition_id": definition.id,
                        "definition_version": definition.definition_version,
                        "definition_snapshot": (
                            definition._definition_snapshot()
                        ),
                        "result_kind": "technical",
                    })
                continue
            for values in evaluated:
                definition = definitions_by_code.get(values["control_code"])
                if definition:
                    candidates.append(definition._apply_hygiene_policy(values))
        return candidates

    @api.model
    def _run_hygiene_evaluator(self, evaluator_key, evaluator_name, company):
        if not evaluator_name or not hasattr(self, evaluator_name):
            message = (
                f"No installed evaluator is registered for {evaluator_key}."
            )
            raise UserError(message)
        return getattr(self, evaluator_name)(company)

    @api.model
    def sync_for_company(self, company):
        company.ensure_one()
        candidate_values = self._candidate_values(company)
        candidates_by_key = {
            values["issue_key"]: values for values in candidate_values
        }
        existing = {
            issue.issue_key: issue
            for issue in self.search([("company_id", "=", company.id)])
        }
        for issue_key, values in candidates_by_key.items():
            issue = existing.get(issue_key)
            if not issue:
                self.create(values)
                continue
            if issue.status == "dismissed":
                issue.write({"last_detected_at": values["last_detected_at"]})
                continue
            values.update({"status": "open", "resolved_at": False})
            issue.write(values)
        resolved_at = fields.Datetime.now()
        for issue_key, issue in existing.items():
            if issue_key not in candidates_by_key and issue.status != "resolved":
                issue.write({"status": "resolved", "resolved_at": resolved_at})
        return self.search([("company_id", "=", company.id)])

    def action_open_source(self):
        self.ensure_one()
        if not self.target_model or not self.target_res_id:
            raise UserError("No source record is linked to this issue.")
        if self.target_model not in self.env.registry:
            raise UserError("The linked source model is not available.")
        target_ids = json.loads(self.target_res_ids_json or "[]") or [self.target_res_id]
        view_mode = "list,form" if len(target_ids) > 1 else "form"
        return {
            "type": "ir.actions.act_window",
            "name": self.source_label or self.title,
            "res_model": self.target_model,
            "res_id": self.target_res_id if len(target_ids) == 1 else False,
            "view_mode": view_mode,
            "views": [(False, mode) for mode in view_mode.split(",")],
            "domain": [("id", "in", target_ids)],
            "target": "current",
            "context": {"create": False, "delete": False},
        }

    def action_check_resolution(self):
        if not self.env.user.has_group("account.group_account_manager"):
            raise AccessError("Only an Accounting Manager can refresh Accounting Hygiene.")
        companies = self.company_id
        for company in companies:
            self.sync_for_company(company)
        self.invalidate_recordset()
        unresolved = self.filtered(lambda issue: issue.status == "open")
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Resolution checked",
                "message": (
                    "The underlying condition still needs attention."
                    if unresolved
                    else "The underlying condition is resolved."
                ),
                "type": "warning" if unresolved else "success",
                "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "reload"},
            },
        }

    def action_dismiss(self):
        if not self.env.user.has_group("account.group_account_manager"):
            raise AccessError("Only an Accounting Manager can dismiss a Hygiene issue.")
        self.write({
            "status": "dismissed",
            "dismissed_at": fields.Datetime.now(),
            "dismissed_by_id": self.env.user.id,
        })
        return {"type": "ir.actions.client", "tag": "reload"}
