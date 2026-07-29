import hashlib
import json

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError


class AccountAccount(models.Model):
    _inherit = "account.account"

    rebuild_entry_direction_policy = fields.Selection(
        [
            ("auto", "Automatic from French Account Code"),
            ("none", "No Direction Check"),
            ("debit", "Normally Debit"),
            ("credit", "Normally Credit"),
        ],
        string="Entry Direction Check",
        required=True,
        default="auto",
        tracking=True,
        help=(
            "Warn before posting a journal item on the exceptional side of "
            "this account. Automatic mode protects French exchange-loss "
            "accounts 666 as normally debit and exchange-gain accounts 766 "
            "as normally credit. Native exchange adjustments, refunds and "
            "formal reversals remain allowed."
        ),
    )

    def _rebuild_expected_entry_direction(self):
        self.ensure_one()
        policy = self.rebuild_entry_direction_policy
        if policy in {"debit", "credit"}:
            return policy
        if policy == "auto":
            code = self.code or ""
            if code.startswith("666"):
                return "debit"
            if code.startswith("766"):
                return "credit"
        return False


class AccountMove(models.Model):
    _inherit = "account.move"

    rebuild_direction_exception_signature = fields.Char(
        copy=False,
        readonly=True,
    )
    rebuild_direction_exception_required = fields.Boolean(
        compute="_compute_rebuild_direction_exception",
    )
    rebuild_direction_exception_confirmed = fields.Boolean(
        compute="_compute_rebuild_direction_exception",
    )
    rebuild_direction_exception_message = fields.Text(
        compute="_compute_rebuild_direction_exception",
    )

    def _rebuild_direction_guard_is_exempt(self):
        self.ensure_one()
        if (
            self.move_type in {"out_refund", "in_refund"}
            or self.reversed_entry_id
            or self.rebuild_source_id
            or self.env.context.get("no_exchange_difference")
        ):
            return True
        return bool(
            self.id
            and self.env["account.partial.reconcile"].search_count(
                [("exchange_move_id", "=", self.id)],
                limit=1,
            ),
        )

    def _rebuild_direction_guard_violations(self):
        self.ensure_one()
        if self.state != "draft" or self._rebuild_direction_guard_is_exempt():
            return self.env["account.move.line"]
        return self.line_ids.filtered(
            lambda line: (
                line.display_type not in {"line_section", "line_subsection", "line_note"}
                and (
                    (
                        line.account_id._rebuild_expected_entry_direction() == "debit"
                        and line.credit > 0
                    )
                    or (
                        line.account_id._rebuild_expected_entry_direction() == "credit"
                        and line.debit > 0
                    )
                )
            ),
        )

    def _rebuild_direction_guard_signature(self, violations=None):
        self.ensure_one()
        violations = violations or self._rebuild_direction_guard_violations()
        if not violations:
            return False
        payload = [
            (
                line.id,
                line.account_id.id,
                line.account_id._rebuild_expected_entry_direction(),
                line.debit,
                line.credit,
            )
            for line in violations.sorted("id")
        ]
        return hashlib.sha256(
            json.dumps(payload, separators=(",", ":")).encode(),
        ).hexdigest()

    def _rebuild_direction_guard_message(self, violations):
        self.ensure_one()
        descriptions = []
        for line in violations:
            expected = line.account_id._rebuild_expected_entry_direction()
            actual = "credit" if line.credit > 0 else "debit"
            descriptions.append(
                _(
                    "%(code)s %(name)s is normally %(expected)s, but this "
                    "draft uses it on the %(actual)s side.",
                    code=line.account_id.code,
                    name=line.account_id.name,
                    expected=expected,
                    actual=actual,
                ),
            )
        return "\n".join(descriptions)

    @api.depends(
        "state",
        "move_type",
        "reversed_entry_id",
        "line_ids.account_id",
        "line_ids.debit",
        "line_ids.credit",
        "line_ids.display_type",
        "rebuild_direction_exception_signature",
    )
    def _compute_rebuild_direction_exception(self):
        for move in self:
            violations = move._rebuild_direction_guard_violations()
            current_signature = move._rebuild_direction_guard_signature(violations)
            move.rebuild_direction_exception_required = bool(violations)
            move.rebuild_direction_exception_confirmed = bool(
                current_signature
                and current_signature == move.rebuild_direction_exception_signature,
            )
            move.rebuild_direction_exception_message = (
                move._rebuild_direction_guard_message(violations)
                if violations
                else False
            )

    def action_rebuild_confirm_direction_exception(self):
        self.ensure_one()
        if not self.env.user.has_group("account.group_account_user"):
            raise AccessError(_("Only an Accounting user can confirm this exception."))
        violations = self._rebuild_direction_guard_violations()
        if not violations:
            raise UserError(_("There is no exceptional account direction to confirm."))
        signature = self._rebuild_direction_guard_signature(violations)
        message = self._rebuild_direction_guard_message(violations)
        self.rebuild_direction_exception_signature = signature
        self.message_post(
            body=_(
                "Exceptional account direction confirmed before posting:<br/>%(details)s",
                details="<br/>".join(message.splitlines()),
            ),
            subtype_xmlid="mail.mt_note",
        )
        return True

    def _rebuild_check_direction_guard(self):
        for move in self:
            violations = move._rebuild_direction_guard_violations()
            if not violations:
                continue
            current_signature = move._rebuild_direction_guard_signature(violations)
            if current_signature == move.rebuild_direction_exception_signature:
                continue
            raise UserError(
                _(
                    "Check the account direction before posting.\n\n"
                    "%(details)s\n\n"
                    "Correct the journal item, or use Confirm exceptional "
                    "direction on the draft when this is a justified manual "
                    "correction.",
                    details=move._rebuild_direction_guard_message(violations),
                ),
            )

    def _post(self, soft=True):
        self._rebuild_check_direction_guard()
        return super()._post(soft=soft)
