from odoo import api, fields, models
from odoo.exceptions import UserError


class RebuildAccountReviewDecision(models.Model):
    _name = "rebuild.account.review.decision"
    _description = "USL Accounting Review Decision"
    _order = "reviewed_at desc, id desc"

    name = fields.Char(required=True, default="Accounting Review Decision")
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("recorded", "Recorded"),
            ("superseded", "Superseded"),
        ],
        required=True,
        default="draft",
        index=True,
    )
    gate = fields.Selection(
        [
            ("report_parity", "Report Parity"),
            ("fec_validation", "FEC Validation"),
            ("tax_external_value", "Tax External Value"),
            ("discrepancy_acceptance", "Discrepancy Acceptance"),
            ("scope_exclusion", "Scope Exclusion"),
            ("declaration_review", "Declaration Review"),
            ("closing_review", "Closing Review"),
            ("milestone_closure", "Milestone Closure"),
            ("other", "Other"),
        ],
        required=True,
        default="other",
        index=True,
    )
    conclusion = fields.Selection(
        [
            ("pending", "Pending"),
            ("accepted", "Accepted"),
            ("accepted_with_difference", "Accepted With Difference"),
            ("requires_change", "Requires Change"),
            ("rejected", "Rejected"),
            ("not_applicable", "Not Applicable"),
        ],
        required=True,
        default="pending",
        index=True,
    )
    required_authority = fields.Selection(
        [
            ("valentin", "Valentin"),
            ("accountant", "Accountant"),
            ("legal_or_compliance", "Legal or Compliance Adviser"),
            ("operator", "Finance Operator"),
            ("joint", "Joint Approval"),
        ],
        required=True,
        default="accountant",
        index=True,
    )
    company_id = fields.Many2one("res.company", index=True)
    period_key = fields.Char(index=True)
    source_report_id = fields.Many2one("rebuild.account.source.report", index=True, ondelete="set null")
    discrepancy_id = fields.Many2one("rebuild.account.discrepancy", index=True, ondelete="set null")
    external_value_id = fields.Many2one("rebuild.account.external.report.value", index=True, ondelete="set null")
    reconciliation_review_id = fields.Many2one(
        "rebuild.account.reconciliation.review",
        index=True,
        ondelete="set null",
    )
    declaration_id = fields.Many2one(
        "rebuild.account.declaration",
        index=True,
        ondelete="set null",
    )
    closing_period_id = fields.Many2one(
        "rebuild.account.closing.period",
        index=True,
        ondelete="set null",
    )
    import_run_id = fields.Many2one("rebuild.account.import.run", index=True, ondelete="set null")
    evidence_key = fields.Char(index=True)
    source_value = fields.Text()
    target_value = fields.Text()
    difference = fields.Text()
    decision_summary = fields.Text(required=True, default="Pending review.")
    evidence_summary = fields.Text()
    remaining_risk = fields.Text()
    next_action = fields.Text()
    reviewer_name = fields.Char()
    reviewer_user_id = fields.Many2one(
        "res.users",
        default=lambda self: self.env.user,
        readonly=True,
    )
    reviewed_at = fields.Datetime(default=fields.Datetime.now, readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name") in (None, "", "Accounting Review Decision"):
                vals["name"] = self._default_name_from_values(vals)
        return super().create(vals_list)

    def write(self, vals):
        protected = self.filtered(lambda decision: decision.state in {"recorded", "superseded"})
        if protected and (set(vals) != {"state"} or vals.get("state") != "superseded"):
            raise UserError(
                "Recorded or superseded accounting review decisions are immutable. "
                "Supersede the decision and create a new review decision instead."
            )
        return super().write(vals)

    @api.model
    def _default_name_from_values(self, vals):
        if vals.get("source_report_id"):
            report = self.env["rebuild.account.source.report"].browse(vals["source_report_id"])
            return f"Report review - {report.display_name}"
        if vals.get("discrepancy_id"):
            discrepancy = self.env["rebuild.account.discrepancy"].browse(vals["discrepancy_id"])
            return f"Discrepancy review - {discrepancy.display_name}"
        if vals.get("external_value_id"):
            external_value = self.env["rebuild.account.external.report.value"].browse(vals["external_value_id"])
            return f"External value review - {external_value.display_name}"
        if vals.get("reconciliation_review_id"):
            reconciliation_review = self.env["rebuild.account.reconciliation.review"].browse(
                vals["reconciliation_review_id"]
            )
            return f"Reconciliation review - {reconciliation_review.display_name}"
        if vals.get("declaration_id"):
            declaration = self.env["rebuild.account.declaration"].browse(vals["declaration_id"])
            return f"Declaration review - {declaration.display_name}"
        if vals.get("closing_period_id"):
            closing = self.env["rebuild.account.closing.period"].browse(vals["closing_period_id"])
            return f"Closing review - {closing.display_name}"
        gate_label = dict(self._fields["gate"].selection).get(vals.get("gate"), "Accounting")
        return f"{gate_label} review"

    def action_record(self):
        for decision in self:
            if not decision.decision_summary or decision.decision_summary == "Pending review.":
                raise UserError("Record a factual decision summary before marking this review decision as recorded.")
            if decision.conclusion == "pending":
                raise UserError("Choose a non-pending conclusion before marking this review decision as recorded.")
            if (
                decision.gate in {"declaration_review", "closing_review"}
                and decision.conclusion in {"accepted", "accepted_with_difference", "not_applicable"}
                and not decision.evidence_summary
            ):
                message = "Record the evidence supporting this declaration or closing acceptance decision."
                raise UserError(message)
            if (
                decision.gate == "closing_review"
                and decision.conclusion in {
                    "accepted",
                    "accepted_with_difference",
                }
                and (
                    not decision.closing_period_id
                    or not decision.closing_period_id.package_attachment_ids
                )
            ):
                message = (
                    "Attach at least one generated closing package before "
                    "recording an accepted closing decision."
                )
                raise UserError(message)
            if decision.gate == "closing_review" and decision.closing_period_id:
                previous = self.search([
                    ("id", "!=", decision.id),
                    (
                        "closing_period_id",
                        "=",
                        decision.closing_period_id.id,
                    ),
                    ("gate", "=", "closing_review"),
                    ("state", "=", "recorded"),
                ])
                previous.write({"state": "superseded"})
            decision.write({
                "state": "recorded",
                "reviewed_at": fields.Datetime.now(),
                "reviewer_user_id": self.env.user.id,
                "reviewer_name": decision.reviewer_name or self.env.user.name,
            })
            decision._apply_recorded_decision()
        return True

    def action_supersede(self):
        closings = self.filtered(
            lambda decision: (
                decision.state == "recorded"
                and decision.gate == "closing_review"
                and decision.closing_period_id
            ),
        ).mapped("closing_period_id")
        self.write({"state": "superseded"})
        for closing in closings:
            current = self.search([
                ("closing_period_id", "=", closing.id),
                ("gate", "=", "closing_review"),
                ("state", "=", "recorded"),
            ], order="reviewed_at desc, id desc", limit=1)
            if current:
                current._apply_closing_decision()
            else:
                closing.sudo().write({
                    "review_status": "accountant_requested",
                    "state": "blocked",
                })
        return True

    def _apply_recorded_decision(self):
        for decision in self:
            reviewer_name = decision.reviewer_name or decision.reviewer_user_id.name or self.env.user.name
            reviewed_at = decision.reviewed_at or fields.Datetime.now()
            if decision.source_report_id:
                decision._apply_source_report_decision(reviewer_name, reviewed_at)
            if decision.external_value_id:
                decision._apply_external_value_decision(reviewer_name, reviewed_at)
            if decision.discrepancy_id:
                decision._apply_discrepancy_decision(reviewer_name)
            if decision.declaration_id:
                decision._apply_declaration_decision()
            if decision.closing_period_id:
                decision._apply_closing_decision()

    def _apply_source_report_decision(self, reviewer_name, reviewed_at):
        accepted_conclusions = {"accepted", "accepted_with_difference", "not_applicable"}
        vals = {
            "latest_evidence_status": f"recorded_review_decision:{self.conclusion}",
        }
        if self.conclusion in accepted_conclusions:
            vals["parity_level"] = "level_4_accepted"
            vals["parity_gap"] = self.remaining_risk if self.conclusion == "accepted_with_difference" else False
        else:
            vals["parity_gap"] = self.remaining_risk or self.decision_summary
        note_lines = [
            self.source_report_id.note or "",
            (
                f"Recorded review decision on {fields.Datetime.to_string(reviewed_at)} by {reviewer_name}: "
                f"{dict(self._fields['conclusion'].selection).get(self.conclusion, self.conclusion)} - "
                f"{self.decision_summary}"
            ),
        ]
        vals["note"] = "\n".join(line for line in note_lines if line)
        self.source_report_id.sudo().write(vals)

    def _apply_external_value_decision(self, reviewer_name, reviewed_at):
        status_by_conclusion = {
            "accepted": "accepted",
            "accepted_with_difference": "accepted_with_difference",
            "requires_change": "rejected",
            "rejected": "rejected",
            "not_applicable": "superseded",
        }
        status = status_by_conclusion.get(self.conclusion)
        if not status:
            return
        self.external_value_id.sudo().write({
            "review_status": status,
            "decision": self.decision_summary,
            "reviewer_name": reviewer_name,
            "reviewed_at": reviewed_at,
        })

    def _apply_discrepancy_decision(self, reviewer_name):
        status_by_conclusion = {
            "accepted": "accepted",
            "accepted_with_difference": "accepted",
            "not_applicable": "accepted",
            "requires_change": "investigating",
            "rejected": "open",
        }
        status = status_by_conclusion.get(self.conclusion)
        if not status:
            return
        self.discrepancy_id.sudo().write({
            "status": status,
            "decision": self.decision_summary,
            "approver": reviewer_name,
        })

    def _apply_declaration_decision(self):
        review_status = {
            "accepted": "accepted",
            "accepted_with_difference": "accepted_with_difference",
            "requires_change": "rejected",
            "rejected": "rejected",
            "not_applicable": "accepted_with_difference",
        }.get(self.conclusion)
        if not review_status:
            return
        vals = {"review_status": review_status}
        if self.conclusion in {"accepted", "accepted_with_difference", "not_applicable"}:
            vals["status"] = "ready_to_file"
        else:
            vals["status"] = "data_missing"
        self.declaration_id.sudo().write(vals)

    def _apply_closing_decision(self):
        review_status = {
            "accepted": "accepted",
            "accepted_with_difference": "accepted_with_difference",
            "requires_change": "rejected",
            "rejected": "rejected",
            "not_applicable": "rejected",
        }.get(self.conclusion)
        if not review_status:
            return
        accepted = review_status in {"accepted", "accepted_with_difference"}
        self.closing_period_id.sudo().write({
            "review_status": review_status,
            "state": "ready" if accepted and not self.closing_period_id.blocking_count else "blocked",
        })
        if accepted:
            self.closing_period_id.sudo()._capture_accepted_snapshots(self)

    def action_open_source_report(self):
        self.ensure_one()
        if not self.source_report_id:
            raise UserError("This review decision is not linked to a source report.")
        return {
            "type": "ir.actions.act_window",
            "name": "Source Accounting Report",
            "res_model": "rebuild.account.source.report",
            "res_id": self.source_report_id.id,
            "view_mode": "form",
            "context": {"create": False, "delete": False},
        }

    def action_open_discrepancy(self):
        self.ensure_one()
        if not self.discrepancy_id:
            raise UserError("This review decision is not linked to a discrepancy.")
        return {
            "type": "ir.actions.act_window",
            "name": "Accounting Discrepancy",
            "res_model": "rebuild.account.discrepancy",
            "res_id": self.discrepancy_id.id,
            "view_mode": "form",
            "context": {"create": False, "delete": False},
        }

    def action_open_external_value(self):
        self.ensure_one()
        if not self.external_value_id:
            raise UserError("This review decision is not linked to an external report value.")
        return {
            "type": "ir.actions.act_window",
            "name": "External Report Value",
            "res_model": "rebuild.account.external.report.value",
            "res_id": self.external_value_id.id,
            "view_mode": "form",
            "context": {"create": False, "delete": False},
        }

    def action_open_reconciliation_review(self):
        self.ensure_one()
        if not self.reconciliation_review_id:
            raise UserError("This review decision is not linked to a reconciliation boundary review.")
        return {
            "type": "ir.actions.act_window",
            "name": "Reconciliation Boundary Review",
            "res_model": "rebuild.account.reconciliation.review",
            "res_id": self.reconciliation_review_id.id,
            "view_mode": "form",
            "context": {"create": False, "delete": False},
        }
