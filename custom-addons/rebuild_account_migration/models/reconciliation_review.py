from odoo import fields, models
from odoo.exceptions import UserError


class RebuildAccountReconciliationReview(models.Model):
    _name = "rebuild.account.reconciliation.review"
    _description = "USL Source Reconciliation Boundary Review"
    _inherit = ["rebuild.source.trace.mixin"]
    _order = "max_date, reconciliation_kind, source_partial_reconcile_id, source_full_reconcile_id"

    name = fields.Char(required=True, index=True)
    reconciliation_kind = fields.Selection(
        [
            ("partial", "Partial Reconciliation"),
            ("full", "Full Reconciliation"),
        ],
        required=True,
        index=True,
    )
    review_status = fields.Selection(
        [
            ("represented_review_only", "Represented - Review Only"),
            ("review_required", "Review Required"),
            ("native_reconciliation_applied", "Native Reconciliation Applied"),
        ],
        default="review_required",
        index=True,
    )
    accounting_effect = fields.Selection(
        [
            ("review_only_cross_boundary", "Review Only - Cross-boundary Source Reconciliation"),
        ],
        default="review_only_cross_boundary",
        required=True,
        index=True,
    )
    company_id = fields.Many2one("res.company", required=True, index=True)
    source_company_id = fields.Integer(string="Primary Source Company", index=True, copy=False)
    source_company_ids = fields.Char(string="Source Companies", copy=False)
    company_currency_id = fields.Many2one(
        related="company_id.currency_id",
        string="Company Currency",
        readonly=True,
    )

    source_partial_reconcile_id = fields.Integer(index=True, copy=False)
    source_full_reconcile_id = fields.Integer(index=True, copy=False)
    source_debit_line_id = fields.Integer(index=True, copy=False)
    source_credit_line_id = fields.Integer(index=True, copy=False)
    source_debit_move_id = fields.Integer(index=True, copy=False)
    source_credit_move_id = fields.Integer(index=True, copy=False)
    source_debit_move_date = fields.Date(index=True)
    source_credit_move_date = fields.Date(index=True)
    source_debit_move_state = fields.Char(index=True)
    source_credit_move_state = fields.Char(index=True)
    source_debit_company_id = fields.Integer(index=True, copy=False)
    source_credit_company_id = fields.Integer(index=True, copy=False)
    debit_line_imported = fields.Boolean(copy=False)
    credit_line_imported = fields.Boolean(copy=False)
    debit_move_line_id = fields.Many2one("account.move.line", index=True, ondelete="set null")
    credit_move_line_id = fields.Many2one("account.move.line", index=True, ondelete="set null")

    source_exchange_move_id = fields.Integer(index=True, copy=False)
    exchange_move_imported = fields.Boolean(copy=False)
    exchange_move_id = fields.Many2one("account.move", index=True, ondelete="set null")
    max_date = fields.Date(index=True)
    amount = fields.Monetary(currency_field="company_currency_id")
    debit_amount_currency = fields.Monetary(currency_field="company_currency_id")
    credit_amount_currency = fields.Monetary(currency_field="company_currency_id")

    total_line_count = fields.Integer(copy=False)
    imported_line_count = fields.Integer(copy=False)
    missing_line_count = fields.Integer(copy=False)
    source_line_ids = fields.Text(copy=False)
    imported_source_line_ids = fields.Text(copy=False)
    missing_source_line_ids = fields.Text(copy=False)
    missing_source_move_ids = fields.Text(copy=False)
    missing_source_move_states = fields.Text(copy=False)
    missing_source_move_dates = fields.Text(copy=False)
    missing_source_company_ids = fields.Text(copy=False)
    generated_missing_line_count = fields.Integer(string="Generated Missing Endpoint Lines", copy=False)
    generated_missing_source_line_ids = fields.Text(string="Generated Missing Source Lines", copy=False)
    missing_endpoint_coverage = fields.Selection(
        [
            ("none_required", "No Missing Endpoint"),
            ("all_generated_draft", "All Missing Endpoints Generated as Draft"),
            ("partial_generated_draft", "Some Missing Endpoints Generated as Draft"),
            ("not_generated", "Missing Endpoints Not Generated"),
        ],
        default="not_generated",
        index=True,
        copy=False,
    )
    source_partial_reconcile_ids = fields.Text(string="Source Partial Reconciliations", copy=False)
    note = fields.Text()

    def _source_id_values(self, *values):
        source_ids = []
        for value in values:
            source_ids.extend(
                int(item)
                for item in (value or "").replace(" ", "").split(",")
                if item.isdigit()
            )
        return sorted(set(source_ids))

    def _decision_evidence_key(self):
        self.ensure_one()
        if self.reconciliation_kind == "partial":
            return f"source_partial_reconcile:{self.source_partial_reconcile_id}"
        return f"source_full_reconcile:{self.source_full_reconcile_id}"

    def _append_note(self, message):
        self.ensure_one()
        note_lines = [self.note or "", message]
        self.write({"note": "\n".join(line for line in note_lines if line)})

    def _resolved_endpoint_line(self, source_line_id, direct_line=False):
        if direct_line and direct_line.rebuild_source_id == source_line_id:
            return direct_line
        return self.env["account.move.line"].search([
            ("rebuild_source_snapshot", "=", self.rebuild_source_snapshot),
            ("rebuild_source_id", "=", source_line_id),
            (
                "rebuild_source_model",
                "in",
                ["account.move.line", "account.move.line.document_regeneration"],
            ),
        ], limit=1)

    def _native_partial_endpoint_lines(self):
        self.ensure_one()
        if self.reconciliation_kind != "partial":
            raise UserError("Native application is currently implemented only for partial boundary reconciliations.")
        if not self.source_partial_reconcile_id:
            raise UserError("This boundary review has no source partial reconciliation identifier.")
        debit_line = self._resolved_endpoint_line(self.source_debit_line_id, self.debit_move_line_id)
        credit_line = self._resolved_endpoint_line(self.source_credit_line_id, self.credit_move_line_id)
        if not debit_line or not credit_line:
            raise UserError("Both source reconciliation endpoints must resolve to imported or generated target journal items.")
        if debit_line == credit_line:
            raise UserError("The resolved debit and credit endpoints are the same journal item.")
        if debit_line.company_id != credit_line.company_id:
            raise UserError("The resolved debit and credit endpoints belong to different companies.")
        if debit_line.account_id != credit_line.account_id:
            raise UserError("The resolved debit and credit endpoints use different accounts.")
        return debit_line, credit_line

    def _native_partial_reconcile(self):
        self.ensure_one()
        if not self.source_partial_reconcile_id:
            return self.env["account.partial.reconcile"]
        return self.env["account.partial.reconcile"].search([
            ("rebuild_source_model", "=", "account.partial.reconcile"),
            ("rebuild_source_snapshot", "=", self.rebuild_source_snapshot),
            ("rebuild_source_id", "=", self.source_partial_reconcile_id),
        ], limit=1)

    def _has_recorded_native_reconciliation_decision(self):
        self.ensure_one()
        return bool(self.env["rebuild.account.review.decision"].sudo().search_count([
            ("reconciliation_review_id", "=", self.id),
            ("state", "=", "recorded"),
            ("conclusion", "in", ["accepted", "accepted_with_difference"]),
        ]))

    def _native_partial_action(self, partial):
        return {
            "type": "ir.actions.act_window",
            "name": "Native Partial Reconciliation",
            "res_model": "account.partial.reconcile",
            "view_mode": "list,form",
            "domain": [("id", "=", partial.id)],
            "context": {
                "create": False,
                "edit": False,
                "delete": False,
            },
        }

    def action_open_imported_journal_items(self):
        self.ensure_one()
        source_ids = self._source_id_values(
            self.imported_source_line_ids,
            str(self.source_debit_line_id or ""),
            str(self.source_credit_line_id or ""),
        )
        return {
            "type": "ir.actions.act_window",
            "name": "Imported Reconciliation Endpoint Journal Items",
            "res_model": "account.move.line",
            "view_mode": "list,form,pivot",
            "domain": [
                ("rebuild_source_model", "=", "account.move.line"),
                ("rebuild_source_snapshot", "=", self.rebuild_source_snapshot),
                ("rebuild_source_id", "in", sorted(set(source_ids)) or [0]),
            ],
            "context": {
                "create": False,
                "edit": False,
                "delete": False,
            },
        }

    def action_open_generated_missing_endpoint_items(self):
        self.ensure_one()
        source_ids = self._source_id_values(self.generated_missing_source_line_ids)
        return {
            "type": "ir.actions.act_window",
            "name": "Generated Draft Reconciliation Endpoint Items",
            "res_model": "account.move.line",
            "view_mode": "list,form,pivot",
            "domain": [
                ("rebuild_source_model", "=", "account.move.line.document_regeneration"),
                ("rebuild_source_snapshot", "=", self.rebuild_source_snapshot),
                ("rebuild_source_id", "in", source_ids or [0]),
            ],
            "context": {
                "create": False,
                "edit": False,
                "delete": False,
            },
        }

    def action_preview_native_partial_reconciliation(self):
        self.ensure_one()
        debit_line, credit_line = self._native_partial_endpoint_lines()
        return {
            "type": "ir.actions.act_window",
            "name": "Native Partial Reconciliation Endpoints",
            "res_model": "account.move.line",
            "view_mode": "list,form,pivot",
            "domain": [("id", "in", [debit_line.id, credit_line.id])],
            "context": {
                "create": False,
                "edit": False,
                "delete": False,
                "rebuild_native_partial_amount": float(self.amount or 0.0),
                "rebuild_source_partial_reconcile_id": self.source_partial_reconcile_id,
            },
        }

    def action_preview_native_full_reconciliation_scope(self):
        self.ensure_one()
        if self.reconciliation_kind != "full":
            raise UserError(
                "Full-scope preview is available only for full "
                "reconciliation boundary reviews.",
            )
        source_ids = self._source_id_values(
            self.imported_source_line_ids,
            self.generated_missing_source_line_ids,
        )
        if not source_ids:
            raise UserError(
                "This full reconciliation review has no resolved source "
                "journal-item scope.",
            )
        lines = self.env["account.move.line"].search([
            ("rebuild_source_snapshot", "=", self.rebuild_source_snapshot),
            ("rebuild_source_id", "in", source_ids),
            (
                "rebuild_source_model",
                "in",
                [
                    "account.move.line",
                    "account.move.line.document_regeneration",
                ],
            ),
        ])
        resolved_source_ids = set(lines.mapped("rebuild_source_id"))
        unresolved_source_ids = sorted(set(source_ids) - resolved_source_ids)
        if unresolved_source_ids:
            raise UserError(
                "Full reconciliation scope has unresolved target journal "
                "items for source lines: %s."
                % ", ".join(str(source_id) for source_id in unresolved_source_ids),
            )
        return {
            "type": "ir.actions.act_window",
            "name": "Full Reconciliation Scope",
            "res_model": "account.move.line",
            "view_mode": "list,form,pivot",
            "domain": [("id", "in", sorted(lines.ids))],
            "context": {
                "create": False,
                "edit": False,
                "delete": False,
                "rebuild_source_full_reconcile_id": (
                    self.source_full_reconcile_id
                ),
            },
        }

    def action_record_review_decision(self):
        self.ensure_one()
        if self.reconciliation_kind == "partial":
            decision_summary = (
                "Pending accountant decision for native application of this "
                "cross-boundary partial reconciliation."
            )
            remaining_risk = (
                "Native application changes target residual/reconciliation "
                "presentation for a generated draft endpoint and must not be "
                "done without an accepted review decision."
            )
            next_action = (
                "Review the imported and generated endpoints, then record "
                "whether native partial reconciliation application is "
                "accepted, accepted with difference, rejected or requires "
                "change."
            )
        else:
            decision_summary = (
                "Pending accountant decision for review-only treatment of "
                "this cross-boundary full reconciliation."
            )
            remaining_risk = (
                "Applying a source full-reconciliation graph would require "
                "a separately authorized workflow over generated draft "
                "endpoints; this review action does not apply that graph."
            )
            next_action = (
                "Review the complete imported/generated scope, then accept "
                "the review-only boundary or request a separately designed "
                "and authorized full-reconciliation workflow."
            )
        return {
            "type": "ir.actions.act_window",
            "name": "Record Reconciliation Boundary Decision",
            "res_model": "rebuild.account.review.decision",
            "view_mode": "form",
            "target": "current",
            "context": {
                "default_name": f"Reconciliation review - {self.name}",
                "default_gate": "discrepancy_acceptance",
                "default_conclusion": "pending",
                "default_required_authority": "accountant",
                "default_company_id": self.company_id.id,
                "default_period_key": (
                    self.max_date and self.max_date.isoformat()
                ) or "",
                "default_reconciliation_review_id": self.id,
                "default_import_run_id": self.rebuild_import_run_id.id,
                "default_evidence_key": self._decision_evidence_key(),
                "default_source_value": str(self.source_partial_reconcile_id or self.source_full_reconcile_id or ""),
                "default_target_value": self.missing_endpoint_coverage,
                "default_difference": self.accounting_effect,
                "default_decision_summary": decision_summary,
                "default_evidence_summary": self.rebuild_import_note or self.note or "",
                "default_remaining_risk": remaining_risk,
                "default_next_action": next_action,
            },
        }

    def action_open_review_decisions(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Reconciliation Review Decisions",
            "res_model": "rebuild.account.review.decision",
            "view_mode": "list,form",
            "domain": [("reconciliation_review_id", "=", self.id)],
            "context": {
                "create": False,
                "delete": False,
            },
        }

    def action_apply_native_partial_reconciliation(self):
        self.ensure_one()
        if not self.env.user.has_group("account.group_account_manager"):
            raise UserError("Only an Accounting Manager can apply a native reconciliation boundary decision.")
        if not self._has_recorded_native_reconciliation_decision():
            raise UserError(
                "Record an accepted review decision for this reconciliation "
                "boundary before applying it natively.",
            )
        if self.missing_endpoint_coverage != "all_generated_draft":
            raise UserError("All missing reconciliation endpoints must be generated as target draft lines first.")
        existing = self._native_partial_reconcile()
        if existing:
            return self._native_partial_action(existing)
        debit_line, credit_line = self._native_partial_endpoint_lines()
        vals = {
            "debit_move_id": debit_line.id,
            "credit_move_id": credit_line.id,
            "amount": self.amount,
            "debit_amount_currency": self.debit_amount_currency,
            "credit_amount_currency": self.credit_amount_currency,
            "max_date": self.max_date,
            "company_id": self.company_id.id,
            "rebuild_source_database": self.rebuild_source_database,
            "rebuild_source_model": "account.partial.reconcile",
            "rebuild_source_id": self.source_partial_reconcile_id,
            "rebuild_source_snapshot": self.rebuild_source_snapshot,
            "rebuild_import_run_id": self.rebuild_import_run_id.id,
            "rebuild_import_status": "imported",
            "rebuild_import_note": (
                "Applied from a cross-boundary reconciliation review after a recorded accountant decision."
            ),
        }
        partial = self.env["account.partial.reconcile"].with_context(
            tracking_disable=True,
            check_move_validity=False,
        ).create(vals)
        self.write({"review_status": "native_reconciliation_applied"})
        self._append_note(
            f"Native partial reconciliation {partial.id} applied from source partial "
            f"{self.source_partial_reconcile_id} after recorded review decision.",
        )
        return self._native_partial_action(partial)
