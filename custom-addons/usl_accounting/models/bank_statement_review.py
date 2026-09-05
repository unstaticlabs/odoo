import datetime as dt
import hashlib
import json
from contextlib import contextmanager

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

REVIEW_STATES = [
    ("expected", "Expected"),
    ("processing", "Processing"),
    ("attention", "Needs attention"),
    ("ready", "Ready for review"),
    ("certified", "Certified"),
    ("reopened", "Reopened"),
]

_CERTIFIED_RECONCILIATION_CONTEXT_KEY = "usl_certified_reconciliation_token"
_CERTIFIED_RECONCILIATION_TOKEN = object()
_CERTIFIED_RECONCILIATION_MOVES = "usl_certified_reconciliation_moves"


def _has_certified_reconciliation_token(env, move_ids):
    return (
        env.context.get(_CERTIFIED_RECONCILIATION_CONTEXT_KEY)
        is _CERTIFIED_RECONCILIATION_TOKEN
        and set(move_ids).issubset(
            env.context.get(_CERTIFIED_RECONCILIATION_MOVES, ()),
        )
    )


def is_accounting_operator(user):
    """Return whether ``user`` can perform day-to-day accounting actions.

    In saas~19.3 the Accounting Administrator group is a sibling of the
    accountant group in the same privilege, rather than an implied group.
    Managers must therefore be accepted explicitly.
    """
    return user.has_group("account.group_account_user") or user.has_group(
        "account.group_account_manager",
    )


class AccountBankStatement(models.Model):
    _name = "account.bank.statement"
    _inherit = ["account.bank.statement", "mail.thread", "mail.activity.mixin"]

    ingestion_config_id = fields.Many2one(
        "account.bank.ingestion.config",
        string="Bank export route",
        check_company=True,
        copy=False,
        index=True,
    )
    period_start = fields.Date(copy=False, index=True)
    period_end = fields.Date(copy=False, index=True)
    balances_confirmed = fields.Boolean(copy=False, tracking=True)
    balances_confirmed_by_id = fields.Many2one("res.users", copy=False, readonly=True)
    balances_confirmed_at = fields.Datetime(copy=False, readonly=True)
    cutover_baseline_confirmed = fields.Boolean(copy=False, tracking=True)
    cutover_baseline_by_id = fields.Many2one("res.users", copy=False, readonly=True)
    cutover_baseline_at = fields.Datetime(copy=False, readonly=True)
    accepted_evidence_id = fields.Many2one(
        "account.bank.ingestion.file",
        string="Official bank statement",
        check_company=True,
        copy=False,
        tracking=True,
        domain="[('statement_id', '=', id), ('classification', '=', 'pdf')]",
    )
    certification_state = fields.Selection(
        [("review", "In review"), ("certified", "Certified"), ("reopened", "Reopened")],
        default="review",
        required=True,
        copy=False,
        tracking=True,
    )
    certified_by_id = fields.Many2one("res.users", copy=False, readonly=True)
    certified_at = fields.Datetime(copy=False, readonly=True)
    certification_ids = fields.One2many(
        "account.bank.statement.certification",
        "statement_id",
        readonly=True,
    )
    bank_source_file_ids = fields.One2many(
        "account.bank.ingestion.file",
        "statement_id",
        readonly=True,
    )
    exception_ids = fields.One2many(
        "account.bank.statement.exception",
        "statement_id",
        readonly=True,
    )
    unresolved_exception_count = fields.Integer(
        compute="_compute_bank_review",
        string="Items to review",
    )
    movement_total = fields.Monetary(compute="_compute_bank_review")
    balance_difference = fields.Monetary(compute="_compute_bank_review")
    continuity_status = fields.Selection(
        [
            ("valid", "Continuous"),
            ("broken", "Continuity broken"),
            ("baseline", "Baseline required"),
        ],
        compute="_compute_bank_review",
    )
    previous_certified_statement_id = fields.Many2one(
        "account.bank.statement",
        compute="_compute_bank_review",
    )
    unreconciled_line_count = fields.Integer(compute="_compute_bank_review")
    unidentified_line_count = fields.Integer(compute="_compute_bank_review")
    review_status = fields.Selection(REVIEW_STATES, compute="_compute_bank_review")
    review_blocking_reason = fields.Char(compute="_compute_bank_review")
    review_summary = fields.Char(compute="_compute_bank_review")
    evidence_check_status = fields.Selection(
        [("missing", "Statement needed"), ("ready", "PDF received")],
        compute="_compute_bank_review",
    )
    transaction_check_status = fields.Selection(
        [
            ("missing", "No transactions"),
            ("attention", "Review needed"),
            ("ready", "Complete"),
        ],
        compute="_compute_bank_review",
    )
    balance_check_status = fields.Selection(
        [
            ("unconfirmed", "Confirm balances"),
            ("mismatch", "Does not agree"),
            ("ready", "Agrees"),
        ],
        compute="_compute_bank_review",
    )
    can_certify = fields.Boolean(compute="_compute_bank_review")

    _managed_period_unique = models.UniqueIndex(
        "(ingestion_config_id, period_start, period_end) "
        "WHERE ingestion_config_id IS NOT NULL",
    )

    @api.constrains("period_start", "period_end", "ingestion_config_id")
    def _check_managed_period(self):
        for statement in self.filtered("ingestion_config_id"):
            if not statement.period_start or not statement.period_end:
                raise ValidationError(_("A managed bank statement requires a period."))
            if statement.period_start > statement.period_end:
                raise ValidationError(
                    _("The statement period end must follow its start."),
                )
            if (
                statement.journal_id
                and statement.journal_id != statement.ingestion_config_id.journal_id
            ):
                raise ValidationError(
                    _("The statement must use the configured bank journal."),
                )

    @api.depends(
        "balance_start",
        "balance_end",
        "balance_end_real",
        "balances_confirmed",
        "cutover_baseline_confirmed",
        "accepted_evidence_id",
        "accepted_evidence_id.evidence_status",
        "certification_state",
        "exception_ids.state",
        "line_ids.amount",
        "line_ids.state",
        "line_ids.is_reconciled",
        "line_ids.provider_code",
        "line_ids.provider_account_id",
        "line_ids.provider_transaction_id",
        "period_start",
        "period_end",
    )
    def _compute_bank_review(self):
        for statement in self:
            posted = statement.line_ids.filtered(lambda line: line.state == "posted")
            movement = sum(posted.mapped("amount"))
            statement.movement_total = movement
            statement.unreconciled_line_count = len(
                posted.filtered(lambda line: not line.is_reconciled),
            )
            if not statement.ingestion_config_id:
                statement.balance_difference = 0
                statement.unresolved_exception_count = 0
                statement.unidentified_line_count = 0
                statement.previous_certified_statement_id = False
                statement.continuity_status = False
                statement.review_status = False
                statement.review_blocking_reason = False
                statement.review_summary = False
                statement.evidence_check_status = False
                statement.transaction_check_status = False
                statement.balance_check_status = False
                statement.can_certify = False
                continue

            statement.balance_difference = statement.balance_end_real - (
                statement.balance_start + movement
            )
            statement.unresolved_exception_count = len(
                statement.exception_ids.filtered(
                    lambda exception: exception.state == "open",
                ),
            )
            statement.unidentified_line_count = len(
                posted.filtered(
                    lambda line: (
                        not line.provider_code
                        or not line.provider_account_id
                        or not line.provider_transaction_id
                    ),
                ),
            )
            statement.evidence_check_status = (
                "ready" if statement.accepted_evidence_id else "missing"
            )
            if not posted:
                statement.transaction_check_status = "missing"
            elif (
                statement.unresolved_exception_count
                or statement.unidentified_line_count
            ):
                statement.transaction_check_status = "attention"
            else:
                statement.transaction_check_status = "ready"
            if not statement.balances_confirmed:
                statement.balance_check_status = "unconfirmed"
            elif (
                statement.currency_id.compare_amounts(
                    statement.balance_difference,
                    0,
                )
                != 0
            ):
                statement.balance_check_status = "mismatch"
            else:
                statement.balance_check_status = "ready"
            previous = self.env["account.bank.statement"]
            if statement.ingestion_config_id and statement.period_start:
                previous = self.search(
                    [
                        ("ingestion_config_id", "=", statement.ingestion_config_id.id),
                        ("period_end", "<", statement.period_start),
                        ("certification_state", "=", "certified"),
                    ],
                    order="period_end desc, id desc",
                    limit=1,
                )
            statement.previous_certified_statement_id = previous
            if previous:
                continuous = (
                    statement.currency_id.compare_amounts(
                        previous.balance_end_real,
                        statement.balance_start,
                    )
                    == 0
                    and previous.period_end + dt.timedelta(days=1)
                    == statement.period_start
                )
                statement.continuity_status = "valid" if continuous else "broken"
            elif statement.cutover_baseline_confirmed:
                statement.continuity_status = "valid"
            else:
                statement.continuity_status = "baseline"

            blockers = []
            if not statement.period_start or not statement.period_end:
                blockers.append(_("Confirm the statement period."))
            if not statement.accepted_evidence_id:
                blockers.append(_("Accept the official PDF statement."))
            else:
                blockers.extend(statement._additional_bank_review_blockers())
            if not statement.balances_confirmed:
                blockers.append(_("Confirm the bank-reported balances."))
            if not posted:
                blockers.append(_("No bank transactions are linked to this period."))
            if (
                statement.currency_id.compare_amounts(statement.balance_difference, 0)
                != 0
            ):
                blockers.append(
                    _(
                        "The transactions differ from the bank closing balance by %(amount)s.",
                        amount=statement.balance_difference,
                    ),
                )
            if statement.continuity_status == "broken":
                blockers.append(
                    _(
                        "The opening balance does not continue the preceding certified statement.",
                    ),
                )
            elif statement.continuity_status == "baseline":
                blockers.append(
                    _("An Accounting Manager must confirm the cut-over baseline."),
                )
            if statement.unresolved_exception_count:
                first_issue = statement.exception_ids.filtered(
                    lambda exception: exception.state == "open",
                ).sorted(lambda exception: (exception.create_date, exception.id))[:1]
                blockers.append(
                    _("Review: %(issue)s", issue=first_issue.name)
                    if first_issue
                    else _("Review the items requiring attention."),
                )
            if statement.unidentified_line_count:
                blockers.append(
                    _("Resolve transaction identity for every statement movement."),
                )

            statement.can_certify = bool(statement.ingestion_config_id and not blockers)
            statement.review_blocking_reason = blockers[0] if blockers else False
            if statement.certification_state == "certified":
                statement.review_status = "attention" if blockers else "certified"
            elif statement.certification_state == "reopened":
                statement.review_status = "reopened" if not blockers else "attention"
            elif blockers:
                statement.review_status = "attention"
            else:
                statement.review_status = "ready"
            if statement.review_status == "certified":
                statement.review_summary = _(
                    "This month agrees with the bank and has been certified.",
                )
            elif statement.review_status == "ready":
                statement.review_summary = _(
                    "All bank checks are complete. This month is ready to certify.",
                )
            elif statement.review_status == "reopened":
                statement.review_summary = _(
                    "This month was reopened and must be certified again.",
                )
            elif len(blockers) == 1:
                statement.review_summary = blockers[0]
            else:
                statement.review_summary = _(
                    "%(count)s checks need attention before this month can be certified.",
                    count=len(blockers),
                )

    def _additional_bank_review_blockers(self):
        """Let optional evidence stores add certification prerequisites."""
        self.ensure_one()
        return []

    def _bank_evidence_snapshot_values(self):
        """Return the immutable evidence identity captured by lifecycle events."""
        self.ensure_one()
        evidence = self.accepted_evidence_id
        return {
            "evidence_attachment_id": evidence.attachment_id.id,
            "evidence_sha256": evidence.sha256,
            "paperless_version": evidence.paperless_version,
        }

    def _assert_account_user(self):
        if not is_accounting_operator(self.env.user):
            raise AccessError(_("Only an accountant can perform this action."))

    def _assert_account_manager(self):
        if not self.env.user.has_group("account.group_account_manager"):
            raise AccessError(_("Only an Accounting Manager can perform this action."))

    def action_confirm_cutover_baseline(self):
        self.ensure_one()
        self._assert_account_manager()
        if self.previous_certified_statement_id:
            raise UserError(
                _("A preceding certified statement already provides continuity."),
            )
        self.sudo().with_context(bank_review_internal=True).write(
            {
                "cutover_baseline_confirmed": True,
                "cutover_baseline_by_id": self.env.user.id,
                "cutover_baseline_at": fields.Datetime.now(),
            },
        )
        self.message_post(
            body=_("Cut-over opening balance accepted by the Accounting Manager."),
        )
        return self._bank_review_notification(
            _("Cut-over baseline confirmed."),
            "success",
        )

    def action_open_confirm_balances(self):
        self.ensure_one()
        self._assert_account_user()
        return {
            "type": "ir.actions.act_window",
            "name": _("Confirm bank balances"),
            "res_model": "account.bank.statement.confirm",
            "view_mode": "form",
            "target": "new",
            "context": {"default_statement_id": self.id},
        }

    def action_certify(self):
        self.ensure_one()
        self._assert_account_user()
        self.env.cr.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            [f"account.bank.statement.certify:{self.id}"],
        )
        self.invalidate_recordset()
        if self.certification_state == "certified":
            return self._bank_review_notification(
                _("This statement is already certified."),
                "info",
            )
        if not self.can_certify:
            raise UserError(
                self.review_blocking_reason
                or _("This statement is not ready for certification."),
            )
        identity_values = sorted(
            line.provider_transaction_id or line.unique_import_id or f"odoo:{line.id}"
            for line in self.line_ids.filtered(lambda line: line.state == "posted")
        )
        digest = hashlib.sha256(
            json.dumps(
                identity_values,
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode(),
        ).hexdigest()
        now = fields.Datetime.now()
        self.env["account.bank.statement.certification"].sudo().create(
            {
                "statement_id": self.id,
                "company_id": self.company_id.id,
                "event_type": "certify",
                "user_id": self.env.user.id,
                "event_at": now,
                "period_start": self.period_start,
                "period_end": self.period_end,
                "balance_start": self.balance_start,
                "movement_total": self.movement_total,
                "balance_end_real": self.balance_end_real,
                "transaction_count": len(identity_values),
                "transaction_identity_digest": digest,
                **self._bank_evidence_snapshot_values(),
            },
        )
        self.sudo().with_context(bank_review_internal=True).write(
            {
                "certification_state": "certified",
                "certified_by_id": self.env.user.id,
                "certified_at": now,
            },
        )
        self.message_post(body=_("Bank statement certified."))
        return self._bank_review_notification(_("Bank statement certified."), "success")

    def action_open_reopen(self):
        self.ensure_one()
        self._assert_account_manager()
        if self.certification_state != "certified":
            raise UserError(_("Only a certified statement can be reopened."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Reopen bank statement"),
            "res_model": "account.bank.statement.reopen",
            "view_mode": "form",
            "target": "new",
            "context": {"default_statement_id": self.id},
        }

    def action_open_transactions(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Bank transactions"),
            "res_model": "account.bank.statement.line",
            "view_mode": "list,form",
            "domain": [("statement_id", "=", self.id)],
            "context": {"create": False},
        }

    def action_open_evidence(self):
        self.ensure_one()
        if not self.accepted_evidence_id:
            raise UserError(_("No official bank statement has been accepted yet."))
        return {
            "type": "ir.actions.act_url",
            "url": f"/web/content/{self.accepted_evidence_id.attachment_id.id}?download=1",
            "target": "self",
        }

    def action_open_statement_pdf_upload(self):
        """Keep evidence recovery on the monthly review instead of mail internals."""
        self.ensure_one()
        self._assert_account_user()
        ingestion = self.accepted_evidence_id.ingestion_id
        if not ingestion:
            ingestion = self.bank_source_file_ids.sorted(
                lambda item: (item.ingestion_id.received_at, item.ingestion_id.id),
                reverse=True,
            ).ingestion_id[:1]
        if not ingestion:
            raise UserError(
                _(
                    "No received bank email is linked to this month. Wait for the "
                    "scheduled export or ask an Accounting Manager to check the email setup.",
                ),
            )
        return {
            "type": "ir.actions.act_window",
            "name": _("Add the official statement PDF"),
            "res_model": "account.bank.ingestion.upload",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_ingestion_id": ingestion.id,
                "default_statement_id": self.id,
            },
        }

    def _bank_review_notification(self, message, notification_type):
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "message": message,
                "type": notification_type,
                "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "reload"},
            },
        }

    def write(self, vals):
        accounting_facts = {
            "reference",
            "ingestion_config_id",
            "period_start",
            "period_end",
            "balance_start",
            "balance_end_real",
            "accepted_evidence_id",
            "line_ids",
        }
        lifecycle_fields = {
            "balances_confirmed",
            "balances_confirmed_by_id",
            "balances_confirmed_at",
            "cutover_baseline_confirmed",
            "cutover_baseline_by_id",
            "cutover_baseline_at",
            "certification_state",
            "certified_by_id",
            "certified_at",
        }
        internal = self.env.su and self.env.context.get("bank_review_internal")
        managed = self.filtered("ingestion_config_id") or vals.get(
            "ingestion_config_id",
        )
        if managed and lifecycle_fields.intersection(vals) and not internal:
            raise AccessError(
                _(
                    "Use the bank statement review actions to change verification state.",
                ),
            )
        if accounting_facts.intersection(vals) and any(
            statement.certification_state == "certified" for statement in self
        ):
            raise UserError(
                _(
                    "Reopen the certified statement before changing its bank evidence or balances.",
                ),
            )
        if managed and accounting_facts.intersection(vals) and not internal:
            raise AccessError(
                _(
                    "Use the bank statement review actions to change managed statement facts.",
                ),
            )
        return super().write(vals)

    def unlink(self):
        if any(statement.certification_state == "certified" for statement in self):
            raise UserError(
                _("Certified bank statements cannot be deleted. Reopen them first."),
            )
        return super().unlink()


class AccountBankStatementLine(models.Model):
    _inherit = "account.bank.statement.line"

    provider_code = fields.Char(copy=False, index=True)
    provider_account_id = fields.Char(copy=False, index=True)
    provider_transaction_id = fields.Char(copy=False, index=True)
    provider_identity_kind = fields.Selection(
        [("stable", "Bank identity"), ("approved_fallback", "Approved fallback")],
        copy=False,
    )
    ingestion_file_ids = fields.Many2many(
        "account.bank.ingestion.file",
        "account_bank_line_ingestion_file_rel",
        "line_id",
        "file_id",
        string="Received in",
        copy=False,
    )

    _provider_identity_unique = models.UniqueIndex(
        "(journal_id, provider_code, provider_account_id, provider_transaction_id) "
        "WHERE provider_code IS NOT NULL AND provider_transaction_id IS NOT NULL",
    )

    def _certified_reconciliation_fingerprint(self):
        """Snapshot bank-origin facts that reconciliation may never change."""
        self.ensure_one()
        liquidity_lines, _suspense_lines, _other_lines = self._seek_for_lines()
        return (
            self.statement_id.id,
            self.move_id.id,
            self.journal_id.id,
            self.company_id.id,
            self.currency_id.id,
            self.company_id.currency_id.id,
            self.date,
            self.amount,
            self.payment_ref,
            self.transaction_type,
            json.dumps(self.transaction_details or {}, sort_keys=True, default=str),
            self.unique_import_id,
            self.provider_code,
            self.provider_account_id,
            self.provider_transaction_id,
            self.provider_identity_kind,
            tuple(sorted(self.ingestion_file_ids.ids)),
            tuple(
                (
                    line.account_id.id,
                    line.date,
                    line.balance,
                    line.currency_id.id,
                    line.amount_currency,
                )
                for line in liquidity_lines.sorted(
                    key=lambda item: (item.account_id.id, item.date, item.balance),
                )
            ),
        )

    def _write_reconciliation_metadata(self, vals):
        """Enrich reconciliation metadata without reopening bank evidence.

        Foreign-amount and partner metadata are not facts supplied by the bank
        statement.  Odoo nevertheless rewrites the existing liquidity line
        while synchronizing them.  This private service corridor permits that
        internal rewrite only when the certified bank-origin fingerprint stays
        economically equivalent.
        """
        allowed = self._certified_reconciliation_metadata_fields()
        unsupported = set(vals) - allowed
        if unsupported:
            raise ValidationError(
                _(
                    "Certified reconciliation metadata cannot update: %(fields)s",
                    fields=", ".join(sorted(unsupported)),
                ),
            )
        with self._preserve_certified_bank_facts() as lines:
            return lines.write(vals)

    def _certified_reconciliation_metadata_fields(self):
        return {"partner_id", "foreign_currency_id", "amount_currency"}

    def _certified_reconciliation_fingerprints(self):
        return {
            line.id: line._certified_reconciliation_fingerprint()
            for line in self.filtered(
                lambda item: item.statement_id.certification_state == "certified",
            )
        }

    def _assert_certified_reconciliation_fingerprints(self, before):
        certified = self.filtered(lambda line: line.id in before)
        certified.invalidate_recordset()
        if any(
            line._certified_reconciliation_fingerprint() != before[line.id]
            for line in certified
        ):
            raise UserError(
                _(
                    "Bank matching attempted to change certified bank-statement facts. "
                    "No changes were saved.",
                ),
            )

    @contextmanager
    def _preserve_certified_bank_facts(self):
        """Allow atomic bookkeeping rewrites, not changes to bank evidence.

        Posting state and counterpart identities are bookkeeping. Native
        access, lock and hash checks still run. The opaque permission is scoped
        to these moves; a failed invariant rolls back even if a caller catches
        the exception and continues its transaction.
        """
        with self.env.cr.savepoint():
            before = self._certified_reconciliation_fingerprints()
            yield self.with_context(**{
                _CERTIFIED_RECONCILIATION_CONTEXT_KEY:
                    _CERTIFIED_RECONCILIATION_TOKEN,
                _CERTIFIED_RECONCILIATION_MOVES: tuple(self.move_id.ids),
            })
            self._assert_certified_reconciliation_fingerprints(before)

    def reconcile_bank_line(self):
        with self._preserve_certified_bank_facts() as lines:
            return super(AccountBankStatementLine, lines).reconcile_bank_line()

    def unreconcile_bank_line(self):
        with self._preserve_certified_bank_facts() as lines:
            return super(AccountBankStatementLine, lines).unreconcile_bank_line()

    def action_undo_reconciliation(self):
        with self._preserve_certified_bank_facts() as lines:
            return super(AccountBankStatementLine, lines).action_undo_reconciliation()

    def _check_certified_mutation(self, vals=None):
        protected = {
            "statement_id",
            "date",
            "amount",
            "payment_ref",
            "transaction_type",
            "transaction_details",
            "unique_import_id",
            "provider_code",
            "provider_account_id",
            "provider_transaction_id",
            "provider_identity_kind",
            "ingestion_file_ids",
        }
        if vals is None or protected.intersection(vals):
            if any(
                line.statement_id.certification_state == "certified" for line in self
            ):
                if (
                    vals
                    and protected.intersection(vals) == {"ingestion_file_ids"}
                    and self.env.su
                ):
                    return
                raise UserError(
                    _(
                        "Reopen the certified statement before changing its bank transactions.",
                    ),
                )

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su and any(
            {
                "provider_code",
                "provider_account_id",
                "provider_transaction_id",
            }.intersection(vals)
            for vals in vals_list
        ):
            raise AccessError(
                _(
                    "Bank transaction identities can only be assigned by the verified import service.",
                ),
            )
        return super().create(vals_list)

    def write(self, vals):
        identity_fields = {
            "provider_code",
            "provider_account_id",
            "provider_transaction_id",
            "provider_identity_kind",
            "ingestion_file_ids",
        }
        managed = self.filtered(
            lambda line: line.provider_code or line.statement_id.ingestion_config_id,
        )
        if managed and identity_fields.intersection(vals) and not self.env.su:
            raise AccessError(
                _(
                    "Use the verified bank export actions to change transaction provenance.",
                ),
            )
        self._check_certified_mutation(vals)
        return super().write(vals)

    def unlink(self):
        self._check_certified_mutation()
        return super().unlink()


class AccountMove(models.Model):
    _inherit = "account.move"

    def _check_certified_statement_move(self):
        if any(
            move.statement_line_id.statement_id.certification_state == "certified"
            for move in self
        ):
            raise UserError(
                _(
                    "Reopen the certified bank statement before cancelling or deleting its transaction.",
                ),
            )

    def button_draft(self):
        with self.statement_line_id._preserve_certified_bank_facts() as lines:
            return super(AccountMove, self.with_env(lines.env)).button_draft()

    def button_cancel(self):
        self._check_certified_statement_move()
        return super().button_cancel()

    def write(self, vals):
        if vals.get("state") == "cancel":
            self._check_certified_statement_move()
        if {"date", "journal_id", "statement_line_id"}.intersection(vals):
            self._check_certified_statement_move()
        return super().write(vals)

    def unlink(self):
        self._check_certified_statement_move()
        return super().unlink()


class AccountMoveLine(models.Model):
    _inherit = "account.move.line"

    def _certified_bank_liquidity_lines(self):
        protected = self.env["account.move.line"]
        for move in self.move_id.filtered("statement_line_id"):
            statement_line = move.statement_line_id
            if statement_line.statement_id.certification_state != "certified":
                continue
            liquidity_lines, _suspense_lines, _other_lines = (
                statement_line._seek_for_lines()
            )
            protected |= self & liquidity_lines
        return protected

    @api.model_create_multi
    def create(self, vals_list):
        for values in vals_list:
            move = self.env["account.move"].browse(values.get("move_id"))
            statement_line = move.statement_line_id
            if (
                statement_line.statement_id.certification_state == "certified"
                and values.get("account_id")
                == statement_line.journal_id.default_account_id.id
                and not _has_certified_reconciliation_token(self.env, move.ids)
            ):
                raise UserError(
                    _(
                        "Reopen the certified bank statement before changing its liquidity entry.",
                    ),
                )
        return super().create(vals_list)

    def write(self, vals):
        protected = {
            "move_id",
            "account_id",
            "date",
            "debit",
            "credit",
            "balance",
            "amount_currency",
            "currency_id",
        }
        if (
            protected.intersection(vals)
            and self._certified_bank_liquidity_lines()
            and not _has_certified_reconciliation_token(self.env, self.move_id.ids)
        ):
            raise UserError(
                _(
                    "Reopen the certified bank statement before changing its liquidity entry.",
                ),
            )
        return super().write(vals)

    def unlink(self):
        if (
            self._certified_bank_liquidity_lines()
            and not _has_certified_reconciliation_token(self.env, self.move_id.ids)
        ):
            raise UserError(
                _(
                    "Reopen the certified bank statement before changing its liquidity entry.",
                ),
            )
        return super().unlink()


class AccountBankStatementCertification(models.Model):
    _name = "account.bank.statement.certification"
    _description = "Bank Statement Certification History"
    _order = "event_at desc, id desc"
    _check_company_auto = True

    statement_id = fields.Many2one(
        "account.bank.statement",
        required=True,
        ondelete="restrict",
        check_company=True,
        index=True,
    )
    company_id = fields.Many2one("res.company", required=True, index=True)
    event_type = fields.Selection(
        [("certify", "Certified"), ("reopen", "Reopened")],
        required=True,
    )
    user_id = fields.Many2one("res.users", required=True)
    event_at = fields.Datetime(required=True)
    reason = fields.Text()
    period_start = fields.Date(required=True)
    period_end = fields.Date(required=True)
    currency_id = fields.Many2one(related="statement_id.currency_id", store=True)
    balance_start = fields.Monetary(required=True)
    movement_total = fields.Monetary(required=True)
    balance_end_real = fields.Monetary(required=True)
    transaction_count = fields.Integer(required=True)
    transaction_identity_digest = fields.Char(required=True)
    evidence_attachment_id = fields.Many2one(
        "ir.attachment",
        required=True,
        ondelete="restrict",
    )
    evidence_sha256 = fields.Char(required=True)
    paperless_version = fields.Char()

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError(
                _(
                    "Certification history can only be created by a statement review action.",
                ),
            )
        return super().create(vals_list)

    def write(self, vals):
        raise AccessError(_("Certification history is immutable."))

    def unlink(self):
        raise AccessError(_("Certification history cannot be deleted."))


class AccountBankStatementException(models.Model):
    _name = "account.bank.statement.exception"
    _description = "Bank Statement Review Exception"
    _order = "create_date desc, id desc"
    _check_company_auto = True

    statement_id = fields.Many2one(
        "account.bank.statement",
        ondelete="restrict",
        check_company=True,
        index=True,
    )
    ingestion_id = fields.Many2one(
        "account.bank.ingestion",
        required=True,
        ondelete="restrict",
        check_company=True,
        index=True,
    )
    file_id = fields.Many2one(
        "account.bank.ingestion.file",
        ondelete="restrict",
        check_company=True,
        index=True,
    )
    company_id = fields.Many2one("res.company", required=True, index=True)
    kind = fields.Selection(
        [
            ("import", "Import failed"),
            ("unsupported", "Unsupported file"),
            ("identity", "Transaction identity needs review"),
            ("evidence", "Evidence needs review"),
            ("account", "Bank account mismatch"),
        ],
        required=True,
        index=True,
    )
    name = fields.Char(required=True)
    detail = fields.Text(required=True)
    state = fields.Selection(
        [("open", "Open"), ("resolved", "Resolved")],
        default="open",
        required=True,
        index=True,
    )
    candidate_values = fields.Json(copy=False)
    mapped_line_id = fields.Many2one(
        "account.bank.statement.line",
        check_company=True,
        domain="[('company_id', '=', company_id)]",
    )
    resolution = fields.Selection(
        [
            ("corrected_source", "The bank sent a corrected file"),
            ("map_existing", "Link to an existing bank transaction"),
            ("approve_new", "Import as a separate bank transaction"),
            ("accept_evidence", "Use as the official statement"),
            ("not_relevant", "Ignore this file for the monthly check"),
        ],
    )
    resolution_reason = fields.Text()
    resolved_by_id = fields.Many2one("res.users", readonly=True)
    resolved_at = fields.Datetime(readonly=True)

    def action_open_resolution(self):
        self.ensure_one()
        if not self.env.user.has_group("account.group_account_manager"):
            raise AccessError(
                _("Only an Accounting Manager can resolve this review item."),
            )
        return {
            "type": "ir.actions.act_window",
            "name": _("Resolve monthly check issue"),
            "res_model": self._name,
            "res_id": self.id,
            "view_mode": "form",
            "view_id": self.env.ref(
                "usl_accounting.view_bank_statement_exception_resolution_form",
            ).id,
            "target": "new",
        }

    def action_resolve(self):
        for exception in self:
            if not self.env.user.has_group("account.group_account_manager"):
                raise AccessError(
                    _("Only an Accounting Manager can resolve this exception."),
                )
            if not exception.resolution or not exception.resolution_reason:
                raise UserError(_("Choose a resolution and record the reason."))
            if exception.resolution == "map_existing" and not exception.mapped_line_id:
                raise UserError(_("Choose the existing bank transaction."))
            if exception.kind == "identity" and exception.resolution == "approve_new":
                exception._approve_identity_candidate()
            elif (
                exception.kind == "identity" and exception.resolution == "map_existing"
            ):
                exception._map_identity_candidate()
            exception.sudo().with_context(bank_exception_internal=True).write(
                {
                    "state": "resolved",
                    "resolved_by_id": self.env.user.id,
                    "resolved_at": fields.Datetime.now(),
                },
            )
            exception.ingestion_id.sudo().message_post(
                body=_(
                    "Bank statement issue resolved by %(user)s: %(name)s",
                    user=self.env.user.display_name,
                    name=exception.name,
                ),
                author_id=self.env.user.partner_id.id,
            )
            exception.ingestion_id._refresh_processing_state()
        return True

    def _approve_identity_candidate(self):
        self.ensure_one()
        values = dict(self.candidate_values or {})
        if not self.file_id or not values:
            raise UserError(
                _("This exception has no transaction candidate to approve."),
            )
        values.update(
            {
                "provider_identity_kind": "approved_fallback",
                "ingestion_file_ids": [(4, self.file_id.id)],
            },
        )
        statement = self.file_id.sudo()._get_or_create_statement(
            self.ingestion_id.config_id,
            self.file_id.period_start,
            self.file_id.period_end,
            {
                "balance_start": self.file_id.parsed_balance_start,
                "balance_end_real": self.file_id.parsed_balance_end_real,
            },
            self.env["account.bank.statement.line"],
            [values],
        )
        line = statement.line_ids.filtered(
            lambda item: (
                item.provider_transaction_id == values["provider_transaction_id"]
            ),
        )
        self.sudo().with_context(bank_exception_internal=True).write(
            {"statement_id": statement.id, "mapped_line_id": line[:1].id},
        )
        self.file_id.sudo().write(
            {
                "statement_id": statement.id,
                "processing_state": "processed",
                "processing_detail": _(
                    "A manager approved a conservative transaction identity.",
                ),
            },
        )
        self.file_id._associate_period_pdfs(statement)

    def _validate_candidate_mapping(self, line, candidate=None):
        self.ensure_one()
        values = candidate or dict(self.candidate_values or {})
        if not values:
            raise UserError(_("This exception has no transaction candidate to map."))
        if line.journal_id != self.ingestion_id.journal_id:
            raise UserError(
                _("The selected transaction belongs to another bank journal."),
            )
        candidate_date = fields.Date.to_date(values.get("date"))
        if (
            line.date != candidate_date
            or line.currency_id.compare_amounts(line.amount, values.get("amount", 0))
            != 0
        ):
            raise UserError(
                _(
                    "The selected transaction does not match the candidate date and amount.",
                ),
            )

    def _map_identity_candidate(self):
        self.ensure_one()
        line = self.mapped_line_id
        self._validate_candidate_mapping(line)
        statement = self.file_id.sudo()._get_or_create_statement(
            self.ingestion_id.config_id,
            self.file_id.period_start,
            self.file_id.period_end,
            {
                "balance_start": self.file_id.parsed_balance_start,
                "balance_end_real": self.file_id.parsed_balance_end_real,
            },
            line,
            [],
        )
        line.sudo().with_context(bank_review_internal=True).write(
            {"ingestion_file_ids": [(4, self.file_id.id)]},
        )
        self.sudo().with_context(bank_exception_internal=True).statement_id = statement
        self.file_id.sudo().write(
            {
                "statement_id": statement.id,
                "processing_state": "processed",
                "processing_detail": _(
                    "A manager linked the transaction to an existing bank entry.",
                ),
            },
        )
        self.file_id._associate_period_pdfs(statement)

    @api.model_create_multi
    def create(self, vals_list):
        if not self.env.su:
            raise AccessError(
                _(
                    "Bank statement exceptions can only be created by the verified import service.",
                ),
            )
        return super().create(vals_list)

    def write(self, vals):
        resolution_fields = {"mapped_line_id", "resolution", "resolution_reason"}
        internal = self.env.su and self.env.context.get("bank_exception_internal")
        if not internal and set(vals) - resolution_fields:
            raise AccessError(
                _("Use the exception resolution action to change review history."),
            )
        if not internal and self.filtered(lambda item: item.state == "resolved"):
            raise AccessError(_("Resolved bank statement exceptions are immutable."))
        return super().write(vals)

    def unlink(self):
        raise AccessError(_("Bank statement exception history cannot be deleted."))
