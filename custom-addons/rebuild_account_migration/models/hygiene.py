import json

from odoo import api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import date_utils


class RebuildAccountHygieneIssue(models.Model):
    _name = "rebuild.account.hygiene.issue"
    _description = "Accounting Hygiene Issue"
    _order = "severity, issue_date desc, id desc"

    _unique_hygiene_issue = models.Constraint(
        "UNIQUE (company_id, issue_key)",
        "An Accounting Hygiene issue key must be unique within one company.",
    )

    issue_key = fields.Char(required=True, index=True, readonly=True)
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
        ],
        required=True,
        index=True,
    )
    severity = fields.Selection(
        [
            ("1_blocking", "Blocking"),
            ("2_warning", "Warning"),
            ("3_attention", "Attention"),
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
    issue_date = fields.Date(index=True)
    amount = fields.Monetary(currency_field="currency_id")
    target_model = fields.Char(readonly=True)
    target_res_id = fields.Integer(readonly=True)
    target_res_ids_json = fields.Text(readonly=True)
    source_label = fields.Char(readonly=True)
    first_detected_at = fields.Datetime(required=True, default=fields.Datetime.now, readonly=True)
    last_detected_at = fields.Datetime(required=True, default=fields.Datetime.now, readonly=True)
    resolved_at = fields.Datetime(readonly=True)
    dismissed_at = fields.Datetime(readonly=True)
    dismissed_by_id = fields.Many2one("res.users", readonly=True)

    @api.model
    def _issue_values(
        self,
        company,
        issue_key,
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
    def _candidate_values(self, company):
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
            candidates.append(self._issue_values(
                company,
                "bank:unmatched",
                "reconciliation",
                "2_warning",
                f"Match {len(bank_lines)} bank transactions",
                "These bank transactions still have no complete accounting counterpart.",
                "Until they are matched, cash movements and their business purpose remain disconnected.",
                "Open the affected transactions, then use Bank Matching with the closest amount and date suggestions to match or categorize each one.",
                "Cash may be correct while counterpart accounts, partners, taxes or open-item statuses remain incomplete.",
                f"{len(bank_lines)} unreconciled bank lines; combined absolute residual {residual:.2f} {company.currency_id.name}.",
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
            total = sum(
                abs(value)
                for value in missing_vendor_evidence.mapped("amount_total_signed")
            )
            candidates.append(self._issue_values(
                company,
                "vendor-evidence:missing",
                "evidence",
                "2_warning",
                f"Add evidence to {len(missing_vendor_evidence)} supplier documents",
                "These new supplier documents have no main invoice, receipt or supporting file attached.",
                "The accounting entries can be correct but remain difficult to review or substantiate.",
                "Open the affected documents and attach each supplier invoice, receipt or a note explaining why evidence is unavailable.",
                "Deductibility, VAT support and the audit trail may be challenged without source evidence.",
                f"{len(missing_vendor_evidence)} non-imported supplier documents; combined absolute total {total:.2f} {company.currency_id.name}. Imported source documents are excluded because historical attachments were deliberately outside dump parity.",
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
            total = sum(
                abs(value)
                for value in stale_documents.mapped("amount_total_signed")
            )
            candidates.append(self._issue_values(
                company,
                "stale-draft:documents",
                "draft",
                "3_attention",
                f"Finish or discard {len(stale_documents)} stale draft documents",
                "These draft business documents have remained unposted for more than 30 days.",
                "Old drafts are easily forgotten and may hide missing liabilities, receivables or corrections.",
                "Open the affected drafts, correct their business fields and taxes, then post or cancel each one with a documented reason.",
                "The ledger and reports exclude drafts unless explicitly requested, so the period may appear incomplete.",
                f"{len(stale_documents)} drafts older than {cutoff}; combined absolute total {total:.2f} {company.currency_id.name}.",
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
            total = sum(
                abs(value)
                for value in missing_expense_evidence.mapped("total_amount")
            )
            candidates.append(self._issue_values(
                company,
                "expense-evidence:missing",
                "evidence",
                "2_warning",
                f"Add receipts to {len(missing_expense_evidence)} expenses",
                "These new expenses have no receipt or supporting file attached.",
                "Receipts are needed to review business purpose, tax and reimbursable amounts.",
                "Open the affected expenses and attach each receipt, or document why evidence cannot be obtained.",
                "Expenses and deductible VAT may be unsupported.",
                f"{len(missing_expense_evidence)} non-imported expenses; combined company-currency total {total:.2f} {company.currency_id.name}. Imported expenses are excluded because historical attachments were deliberately outside dump parity.",
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
            total = sum(
                abs(value) for value in stale_expenses.mapped("total_amount")
            )
            candidates.append(self._issue_values(
                company,
                "stale-expense:workflow",
                "draft",
                "3_attention",
                f"Continue {len(stale_expenses)} stale expense workflows",
                "These expense workflows have not reached accounting for more than 30 days.",
                "Unfinished expenses can delay reimbursement and omit costs from management reporting.",
                "Open the affected expenses, complete their fields and evidence, then submit, approve or refuse them.",
                "Expense, VAT, payable and analytic reporting may remain incomplete.",
                f"{len(stale_expenses)} expenses older than {cutoff}; combined company-currency total {total:.2f} {company.currency_id.name}.",
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
            total = sum(abs(value) for value in analytic_lines.mapped("balance"))
            candidates.append(self._issue_values(
                company,
                "analytic:unallocated",
                "analytic",
                "3_attention",
                f"Review analytic allocation on {len(analytic_lines)} journal items",
                "These posted profit-and-loss lines have no analytic distribution.",
                "General accounting remains balanced, but activity and brand reporting is incomplete.",
                "Open the affected journal items or source documents and add the appropriate analytic allocation where the line is in scope.",
                "Revenue vs Spending and analytic profitability omit these amounts from configured dimensions.",
                f"{len(analytic_lines)} posted lines; combined absolute balance {total:.2f} {company.currency_id.name}.",
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
