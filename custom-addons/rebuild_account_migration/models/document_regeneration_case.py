from odoo import Command, fields, models
from odoo.exceptions import UserError


class RebuildAccountDocumentRegenerationCase(models.Model):
    _name = "rebuild.account.document.regeneration.case"
    _description = "USL Document Regeneration Case"
    _inherit = ["rebuild.source.trace.mixin"]
    _order = "date, source_move_id"

    name = fields.Char(required=True, index=True)
    active = fields.Boolean(default=True, index=True)
    move_review_id = fields.Many2one(
        "rebuild.account.move.review",
        required=True,
        index=True,
        ondelete="cascade",
    )
    company_id = fields.Many2one(
        related="move_review_id.company_id",
        store=True,
        readonly=True,
    )
    journal_id = fields.Many2one(
        related="move_review_id.journal_id",
        store=True,
        readonly=True,
    )
    partner_id = fields.Many2one(
        related="move_review_id.partner_id",
        store=True,
        readonly=True,
    )
    currency_id = fields.Many2one(
        related="move_review_id.currency_id",
        store=True,
        readonly=True,
    )
    company_currency_id = fields.Many2one(
        related="company_id.currency_id",
        string="Company Currency",
        readonly=True,
    )
    source_move_id = fields.Integer(
        related="move_review_id.source_move_id",
        store=True,
        readonly=True,
    )
    source_name = fields.Char(
        related="move_review_id.source_name",
        store=True,
        readonly=True,
    )
    source_state = fields.Char(
        related="move_review_id.source_state",
        store=True,
        readonly=True,
    )
    move_type = fields.Char(
        related="move_review_id.move_type",
        store=True,
        readonly=True,
    )
    date = fields.Date(
        related="move_review_id.date",
        store=True,
        readonly=True,
    )
    invoice_date = fields.Date(
        related="move_review_id.invoice_date",
        store=True,
        readonly=True,
    )
    amount_total_signed = fields.Monetary(
        related="move_review_id.amount_total_signed",
        currency_field="company_currency_id",
        store=True,
        readonly=True,
    )
    amount_residual_signed = fields.Monetary(
        related="move_review_id.amount_residual_signed",
        currency_field="company_currency_id",
        store=True,
        readonly=True,
    )
    source_line_count = fields.Integer(
        related="move_review_id.source_line_count",
        store=True,
        readonly=True,
    )
    source_accounting_line_count = fields.Integer(
        related="move_review_id.source_accounting_line_count",
        store=True,
        readonly=True,
    )
    source_line_review_count = fields.Integer(
        string="Source Line Review Count",
        compute="_compute_source_line_review_count",
    )
    generation_scope = fields.Selection(
        [
            ("draft_business_document", "Draft Business Document"),
            ("draft_journal_entry", "Draft Journal Entry"),
            ("cancelled_source_record", "Cancelled Source Record"),
            ("unsupported_source_record", "Unsupported Source Record"),
        ],
        required=True,
        index=True,
    )
    case_status = fields.Selection(
        [
            ("candidate_ready", "Candidate - Ready for Isolated Regeneration"),
            ("review_only_cancelled_source", "Review Only - Cancelled Source Record"),
            ("review_only_no_accounting_lines", "Review Only - No Accounting Lines"),
            ("blocked_cancelled_source", "Blocked - Cancelled Source Record"),
            ("blocked_missing_source_lines", "Blocked - Missing Source Lines"),
            ("blocked_non_draft_state", "Blocked - Non-draft State"),
            ("blocked_unsupported_move_type", "Blocked - Unsupported Move Type"),
        ],
        required=True,
        index=True,
    )
    generation_status = fields.Selection(
        [
            ("not_generated", "Not Generated"),
            ("not_applicable", "Not Applicable"),
            ("blocked", "Blocked"),
            ("generated", "Generated"),
            ("validated", "Validated"),
            ("mismatch", "Mismatch"),
        ],
        required=True,
        default="not_generated",
        index=True,
    )
    target_move_id = fields.Many2one("account.move", index=True, ondelete="set null")
    generated_line_count = fields.Integer(readonly=True)
    generated_debit_total = fields.Monetary(currency_field="company_currency_id", readonly=True)
    generated_credit_total = fields.Monetary(currency_field="company_currency_id", readonly=True)
    generated_balance_total = fields.Monetary(currency_field="company_currency_id", readonly=True)
    blocker_reason = fields.Text()
    recommended_action = fields.Text()
    validation_note = fields.Text()

    def _compute_source_line_review_count(self):
        for case in self:
            case.source_line_review_count = case.move_review_id.move_line_review_count

    @classmethod
    def _classification_from_review(cls, review):
        invoice_move_types = {
            "out_invoice",
            "out_refund",
            "in_invoice",
            "in_refund",
            "out_receipt",
            "in_receipt",
        }
        if review.state == "cancel" or review.source_state == "cancel":
            return {
                "generation_scope": "cancelled_source_record",
                "case_status": "review_only_cancelled_source",
                "generation_status": "not_applicable",
                "blocker_reason": "The source move is cancelled; exact replay preserves this as review evidence rather than regenerating a live target draft.",
                "recommended_action": "Inspect the source move and source lines. No native draft generation is required unless product or accountant review explicitly requests a cancelled-record scenario.",
            }
        if review.state != "draft" and review.source_state != "draft":
            return {
                "generation_scope": "unsupported_source_record",
                "case_status": "blocked_non_draft_state",
                "generation_status": "blocked",
                "blocker_reason": "Only draft source moves are eligible for native document-regeneration testing in this first workbench.",
                "recommended_action": "Classify the source state before adding this record to document-regeneration mode.",
            }
        if not review.source_accounting_line_count or not review.move_line_review_count:
            currency = review.company_id.currency_id or review.env.company.currency_id
            has_amount = (
                currency.compare_amounts(review.amount_total_signed or 0.0, 0.0) != 0
                or currency.compare_amounts(review.amount_residual_signed or 0.0, 0.0) != 0
                or currency.compare_amounts(review.source_line_debit_total or 0.0, 0.0) != 0
                or currency.compare_amounts(review.source_line_credit_total or 0.0, 0.0) != 0
            )
            if not has_amount:
                return {
                    "generation_scope": "unsupported_source_record",
                    "case_status": "review_only_no_accounting_lines",
                    "generation_status": "not_applicable",
                    "blocker_reason": "The source draft has no preserved accounting lines and no accounting amount to regenerate.",
                    "recommended_action": "Keep this record as workflow review evidence. No native draft generation is required unless the source perimeter is expanded to non-accounting document metadata.",
                }
            return {
                "generation_scope": "unsupported_source_record",
                "case_status": "blocked_missing_source_lines",
                "generation_status": "blocked",
                "blocker_reason": "The source move does not have preserved accounting line reviews sufficient for deterministic regeneration testing.",
                "recommended_action": "Repair the source move-line review import before attempting native document regeneration.",
            }
        if review.move_type in invoice_move_types:
            return {
                "generation_scope": "draft_business_document",
                "case_status": "candidate_ready",
                "generation_status": "not_generated",
                "blocker_reason": "",
                "recommended_action": "Regenerate this record only in an isolated document-regeneration target, then compare generated target lines against the preserved source line reviews.",
            }
        if review.move_type == "entry":
            return {
                "generation_scope": "draft_journal_entry",
                "case_status": "candidate_ready",
                "generation_status": "not_generated",
                "blocker_reason": "",
                "recommended_action": "Regenerate this draft journal entry only in an isolated document-regeneration target, then compare generated target lines against the preserved source line reviews.",
            }
        return {
            "generation_scope": "unsupported_source_record",
            "case_status": "blocked_unsupported_move_type",
            "generation_status": "blocked",
            "blocker_reason": f"Source move type {review.move_type or 'unknown'} is not yet supported by document-regeneration mode.",
            "recommended_action": "Add an explicit regeneration scenario for this move type or record an approved deliberate exclusion.",
        }

    def action_open_source_move_review(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": "Source Move Workflow Review",
            "res_model": "rebuild.account.move.review",
            "view_mode": "form",
            "res_id": self.move_review_id.id,
            "context": {"create": False, "delete": False},
        }

    def action_open_source_line_reviews(self):
        self.ensure_one()
        return self.move_review_id.action_open_source_line_reviews()

    def action_open_generated_move(self):
        self.ensure_one()
        if not self.target_move_id:
            return False
        return {
            "type": "ir.actions.act_window",
            "name": "Generated Draft Document",
            "res_model": "account.move",
            "view_mode": "form",
            "res_id": self.target_move_id.id,
            "context": {"create": False, "delete": False},
        }

    def _source_accounting_line_reviews(self):
        self.ensure_one()
        return self.move_review_id.move_line_review_ids.filtered(
            lambda line: line.accounting_effect == "none_non_posted_source_line"
        ).sorted(lambda line: (line.sequence or 0, line.source_move_line_id or 0, line.id))

    def _trace_values(self, source_model, source_id):
        self.ensure_one()
        return {
            "rebuild_source_database": self.rebuild_source_database,
            "rebuild_source_model": source_model,
            "rebuild_source_id": source_id,
            "rebuild_source_snapshot": self.rebuild_source_snapshot,
            "rebuild_import_run_id": self.rebuild_import_run_id.id,
            "rebuild_import_status": "transformed",
        }

    def _amounts_match(self, source_value, generated_value):
        self.ensure_one()
        currency = self.company_currency_id or self.env.company.currency_id
        return currency.compare_amounts(source_value or 0.0, generated_value or 0.0) == 0

    def _validate_generated_move(self, move):
        self.ensure_one()
        source_lines = self._source_accounting_line_reviews()
        generated_lines = move.line_ids.filtered(lambda line: line.display_type != "line_section")
        source_debit = sum(source_lines.mapped("debit"))
        source_credit = sum(source_lines.mapped("credit"))
        source_balance = sum(source_lines.mapped("balance"))
        generated_debit = sum(generated_lines.mapped("debit"))
        generated_credit = sum(generated_lines.mapped("credit"))
        generated_balance = sum(generated_lines.mapped("balance"))
        line_count_matches = len(source_lines) == len(generated_lines)
        amount_matches = (
            self._amounts_match(source_debit, generated_debit)
            and self._amounts_match(source_credit, generated_credit)
            and self._amounts_match(source_balance, generated_balance)
        )
        status = "validated" if line_count_matches and amount_matches else "mismatch"
        self.write({
            "generation_status": status,
            "target_move_id": move.id,
            "generated_line_count": len(generated_lines),
            "generated_debit_total": generated_debit,
            "generated_credit_total": generated_credit,
            "generated_balance_total": generated_balance,
            "validation_note": (
                "Generated draft line count and debit/credit/balance totals match the preserved source line reviews."
                if status == "validated"
                else (
                    "Generated draft differs from preserved source line reviews. "
                    f"Source lines={len(source_lines)}, generated lines={len(generated_lines)}, "
                    f"source debit={source_debit:.2f}, generated debit={generated_debit:.2f}, "
                    f"source credit={source_credit:.2f}, generated credit={generated_credit:.2f}, "
                    f"source balance={source_balance:.2f}, generated balance={generated_balance:.2f}."
                )
            ),
        })

    def _line_command_from_review(self, line_review):
        self.ensure_one()
        if not line_review.account_id:
            raise UserError(
                "Source move line %s cannot be regenerated because source account %s is not mapped in the target."
                % (line_review.source_move_line_id or line_review.id, line_review.source_account_id or "unknown")
            )
        values = {
            "sequence": line_review.sequence or 10,
            "account_id": line_review.account_id.id,
            "name": line_review.label or line_review.name or "/",
            "ref": line_review.ref,
            "partner_id": line_review.partner_id.id or False,
            "date_maturity": line_review.date_maturity,
            "debit": line_review.debit,
            "credit": line_review.credit,
            "amount_currency": line_review.amount_currency,
            "tax_base_amount": line_review.tax_base_amount,
            "display_type": line_review.display_type or "product",
            **self._trace_values(
                "account.move.line.document_regeneration",
                line_review.source_move_line_id or line_review.rebuild_source_id,
            ),
        }
        if line_review.line_currency_id:
            values["currency_id"] = line_review.line_currency_id.id
        source_tax_ids = [
            int(value)
            for value in (line_review.source_tax_ids or "").replace(" ", "").split(",")
            if value.isdigit()
        ]
        if source_tax_ids:
            taxes = self.env["account.tax"].search([
                ("rebuild_source_model", "=", "account.tax"),
                ("rebuild_source_snapshot", "=", self.rebuild_source_snapshot),
                ("rebuild_source_id", "in", source_tax_ids),
            ])
            if taxes:
                values["tax_ids"] = [Command.set(taxes.ids)]
        source_tag_ids = [
            int(value)
            for value in (line_review.source_tax_tag_ids or "").replace(" ", "").split(",")
            if value.isdigit()
        ]
        if source_tag_ids:
            tags = self.env["account.account.tag"].search([
                ("rebuild_source_model", "=", "account.account.tag"),
                ("rebuild_source_snapshot", "=", self.rebuild_source_snapshot),
                ("rebuild_source_id", "in", source_tag_ids),
            ])
            if tags:
                values["tax_tag_ids"] = [Command.set(tags.ids)]
        if line_review.source_tax_line_id:
            tax_line = self.env["account.tax"].search([
                ("rebuild_source_model", "=", "account.tax"),
                ("rebuild_source_snapshot", "=", self.rebuild_source_snapshot),
                ("rebuild_source_id", "=", line_review.source_tax_line_id),
            ], limit=1)
            if tax_line:
                values["tax_line_id"] = tax_line.id
        if line_review.source_tax_group_id:
            tax_group = self.env["account.tax.group"].search([
                ("rebuild_source_model", "=", "account.tax.group"),
                ("rebuild_source_snapshot", "=", self.rebuild_source_snapshot),
                ("rebuild_source_id", "=", line_review.source_tax_group_id),
            ], limit=1)
            if tax_group:
                values["tax_group_id"] = tax_group.id
        if line_review.source_tax_repartition_line_id:
            repartition_line = self.env["account.tax.repartition.line"].search([
                ("rebuild_source_model", "=", "account.tax.repartition.line"),
                ("rebuild_source_snapshot", "=", self.rebuild_source_snapshot),
                ("rebuild_source_id", "=", line_review.source_tax_repartition_line_id),
            ], limit=1)
            if repartition_line:
                values["tax_repartition_line_id"] = repartition_line.id
        return Command.create(values)

    def action_generate_draft_move(self):
        self.ensure_one()
        if self.case_status != "candidate_ready":
            raise UserError(self.blocker_reason or "This source move is not eligible for draft regeneration.")
        if self.target_move_id:
            self._validate_generated_move(self.target_move_id)
            return self.action_open_generated_move()
        source_lines = self._source_accounting_line_reviews()
        if not source_lines:
            self.write({
                "case_status": "blocked_missing_source_lines",
                "generation_status": "blocked",
                "blocker_reason": "No preserved source accounting line reviews are available for draft generation.",
            })
            raise UserError("No preserved source accounting line reviews are available for draft generation.")
        move_review = self.move_review_id
        Move = self.env["account.move"].with_context(
            check_move_validity=False,
            tracking_disable=True,
            mail_create_nolog=True,
            skip_account_move_synchronization=True,
            skip_invoice_sync=True,
        )
        move = Move.create({
            "move_type": self.move_type if self.move_type in {
                "entry",
                "out_invoice",
                "out_refund",
                "in_invoice",
                "in_refund",
                "out_receipt",
                "in_receipt",
            } else "entry",
            "journal_id": self.journal_id.id,
            "company_id": self.company_id.id,
            "partner_id": self.partner_id.id or False,
            "currency_id": self.currency_id.id,
            "date": self.date,
            "invoice_date": self.invoice_date,
            "invoice_date_due": move_review.invoice_date_due,
            "ref": move_review.ref or move_review.source_name or self.name,
            "payment_reference": move_review.payment_reference,
            "line_ids": [self._line_command_from_review(line_review) for line_review in source_lines],
            **self._trace_values("account.move.document_regeneration", self.source_move_id),
        })
        self._validate_generated_move(move)
        return self.action_open_generated_move()
