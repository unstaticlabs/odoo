from odoo import api, fields, models
from odoo.exceptions import UserError


class RebuildAccountAssuranceDecision(models.Model):
    """Durable business approval for accounting assurance gates."""

    _name = "rebuild.account.assurance.decision"
    _description = "USL Accounting Assurance Decision"
    _order = "reviewed_at desc, id desc"
    _check_company_auto = True

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
            ("fec_validation", "FEC Validation"),
            ("tax_external_value", "Tax External Value"),
            ("declaration_review", "Declaration Review"),
            ("closing_review", "Closing Review"),
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
            ("valentin", "Accounting Manager"),
            ("accountant", "Accountant Reviewer"),
            ("legal_or_compliance", "Legal or Compliance Adviser"),
            ("operator", "Finance Operator"),
            ("joint", "Joint Approval"),
        ],
        required=True,
        default="accountant",
        index=True,
    )
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    period_key = fields.Char(index=True)
    external_value_id = fields.Many2one(
        "rebuild.account.external.report.value",
        index=True,
        ondelete="set null",
        check_company=True,
    )
    declaration_id = fields.Many2one(
        "rebuild.account.declaration",
        index=True,
        ondelete="set null",
        check_company=True,
    )
    closing_period_id = fields.Many2one(
        "rebuild.account.closing.period",
        index=True,
        ondelete="set null",
        check_company=True,
    )
    evidence_key = fields.Char(index=True)
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
            message = (
                "Recorded or superseded accounting review decisions are immutable. "
                "Supersede the decision and create a new review decision instead."
            )
            raise UserError(message)
        return super().write(vals)

    @api.model
    def _default_name_from_values(self, vals):
        if vals.get("external_value_id"):
            external_value = self.env["rebuild.account.external.report.value"].browse(vals["external_value_id"])
            return f"External value review - {external_value.display_name}"
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
                message = (
                    "Record a factual decision summary before marking this "
                    "review decision as recorded."
                )
                raise UserError(message)
            if decision.conclusion == "pending":
                message = (
                    "Choose a non-pending conclusion before marking this "
                    "review decision as recorded."
                )
                raise UserError(message)
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
            if decision.external_value_id:
                decision._apply_external_value_decision(reviewer_name, reviewed_at)
            if decision.declaration_id:
                decision._apply_declaration_decision()
            if decision.closing_period_id:
                decision._apply_closing_decision()

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

    def action_open_external_value(self):
        self.ensure_one()
        if not self.external_value_id:
            message = (
                "This review decision is not linked to an external report "
                "value."
            )
            raise UserError(message)
        return {
            "type": "ir.actions.act_window",
            "name": "External Report Value",
            "res_model": "rebuild.account.external.report.value",
            "res_id": self.external_value_id.id,
            "view_mode": "form",
            "context": {"create": False, "delete": False},
        }
