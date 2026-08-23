from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError, ValidationError

class RebuildAccountDeferral(models.Model):
    _name = "rebuild.account.deferral"
    _description = "Deferred Expense and Revenue Schedule"
    _order = "start_date desc, id desc"
    _check_company_auto = True

    name = fields.Char(required=True, index=True)
    schedule_type = fields.Selection(
        [
            ("expense", "Deferred Expense"),
            ("revenue", "Deferred Revenue"),
        ],
        required=True,
        default="expense",
        index=True,
    )
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("running", "Running"),
            ("closed", "Closed"),
        ],
        required=True,
        default="draft",
        index=True,
        copy=False,
    )
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
    )
    currency_id = fields.Many2one(
        related="company_id.currency_id",
        store=True,
        readonly=True,
    )
    original_move_id = fields.Many2one(
        "account.move",
        string="Original Bill or Invoice",
        required=True,
        check_company=True,
        index=True,
        ondelete="restrict",
    )
    journal_id = fields.Many2one(
        "account.journal",
        required=True,
        check_company=True,
        index=True,
        domain="[('type', '=', 'general'), ('company_id', '=', company_id)]",
    )
    deferral_account_id = fields.Many2one(
        "account.account",
        required=True,
        check_company=True,
        index=True,
        domain="[('company_ids', 'in', company_id)]",
    )
    start_date = fields.Date(required=True, index=True)
    end_date = fields.Date(required=True, index=True)
    line_ids = fields.One2many(
        "rebuild.account.deferral.line",
        "deferral_id",
        string="Schedule",
        copy=True,
    )
    scheduled_line_count = fields.Integer(
        compute="_compute_progress",
        store=True,
    )
    posted_line_count = fields.Integer(
        compute="_compute_progress",
        store=True,
    )
    remaining_line_count = fields.Integer(
        compute="_compute_progress",
        store=True,
    )
    scheduled_amount = fields.Monetary(
        compute="_compute_progress",
        currency_field="currency_id",
        store=True,
    )
    note = fields.Text()

    @api.depends(
        "line_ids",
        "line_ids.state",
        "line_ids.recognition_balance",
    )
    def _compute_progress(self):
        for deferral in self:
            deferral.scheduled_line_count = len(deferral.line_ids)
            deferral.posted_line_count = len(
                deferral.line_ids.filtered(lambda line: line.state == "posted"),
            )
            deferral.remaining_line_count = len(
                deferral.line_ids.filtered(
                    lambda line: line.state == "scheduled",
                ),
            )
            deferral.scheduled_amount = sum(
                abs(line.recognition_balance)
                for line in deferral.line_ids
            )

    @api.constrains("start_date", "end_date")
    def _check_date_range(self):
        for deferral in self:
            if deferral.start_date > deferral.end_date:
                raise ValidationError(
                    _("The deferral end date must not precede its start date."),
                )

    def action_start(self):
        self.check_access("write")
        for deferral in self:
            if not deferral.line_ids:
                raise UserError(
                    _("Add at least one schedule line before starting a deferral."),
                )
            if deferral.state == "draft":
                deferral.state = "running"
        return True

    def _sync_state(self):
        for deferral in self:
            if (
                deferral.line_ids
                and all(
                    line.state == "posted"
                    for line in deferral.line_ids
                )
            ):
                deferral.state = "closed"
            elif deferral.state != "draft":
                deferral.state = "running"

    def action_post_due(self):
        self.check_access("write")
        cutoff = fields.Date.to_date(
            self.env.context.get("deferral_cutoff_date"),
        ) or fields.Date.context_today(self)
        for deferral in self:
            deferral.action_start()
            due_lines = deferral.line_ids.filtered(
                lambda line: (
                    line.state == "scheduled"
                    and line.date <= cutoff
                ),
            )
            due_lines.action_post()
        return True

    def action_open_original_move(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Original Bill or Invoice"),
            "res_model": "account.move",
            "view_mode": "form",
            "res_id": self.original_move_id.id,
            "context": {"create": False, "delete": False},
        }

    def action_open_posted_entries(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Deferred Entries"),
            "res_model": "account.move",
            "view_mode": "list,form",
            "domain": [("id", "in", self.line_ids.move_id.ids)],
            "context": {"create": False, "delete": False},
        }


class RebuildAccountDeferralLine(models.Model):
    _name = "rebuild.account.deferral.line"
    _description = "Deferred Expense and Revenue Schedule Line"
    _inherit = ["analytic.mixin"]
    _order = "date, sequence, id"
    _check_company_auto = True

    name = fields.Char(required=True)
    sequence = fields.Integer(default=10)
    deferral_id = fields.Many2one(
        "rebuild.account.deferral",
        required=True,
        index=True,
        ondelete="cascade",
    )
    company_id = fields.Many2one(
        related="deferral_id.company_id",
        store=True,
        index=True,
        readonly=True,
    )
    currency_id = fields.Many2one(
        related="deferral_id.currency_id",
        store=True,
        readonly=True,
    )
    date = fields.Date(required=True, index=True)
    phase = fields.Selection(
        [
            ("initial_deferral", "Initial Deferral"),
            ("recognition", "Recognition"),
        ],
        required=True,
        default="recognition",
        index=True,
    )
    recognition_account_id = fields.Many2one(
        "account.account",
        required=True,
        check_company=True,
        index=True,
        domain="[('company_ids', 'in', company_id)]",
    )
    partner_id = fields.Many2one(
        "res.partner",
        check_company=True,
        index=True,
    )
    recognition_balance = fields.Monetary(
        required=True,
        currency_field="currency_id",
    )
    recognition_amount_currency = fields.Monetary(
        required=True,
        currency_field="currency_id",
    )
    deferral_balance = fields.Monetary(
        required=True,
        currency_field="currency_id",
    )
    deferral_amount_currency = fields.Monetary(
        required=True,
        currency_field="currency_id",
    )
    state = fields.Selection(
        [
            ("scheduled", "Scheduled"),
            ("posted", "Posted"),
        ],
        required=True,
        default="scheduled",
        index=True,
        copy=False,
    )
    move_id = fields.Many2one(
        "account.move",
        string="Journal Entry",
        check_company=True,
        index=True,
        readonly=True,
        copy=False,
        ondelete="restrict",
    )
    @api.constrains(
        "recognition_balance",
        "deferral_balance",
        "recognition_amount_currency",
        "deferral_amount_currency",
    )
    def _check_balanced_amounts(self):
        for line in self:
            if not line.currency_id.is_zero(
                line.recognition_balance + line.deferral_balance,
            ):
                raise ValidationError(
                    _("The recognition and deferral balances must offset."),
                )
            if not line.currency_id.is_zero(
                line.recognition_amount_currency
                + line.deferral_amount_currency,
            ):
                raise ValidationError(
                    _("The recognition and deferral currency amounts must offset."),
                )

    def _move_values(self):
        self.ensure_one()
        deferral = self.deferral_id
        return {
            "move_type": "entry",
            "date": self.date,
            "journal_id": deferral.journal_id.id,
            "company_id": deferral.company_id.id,
            "currency_id": self.currency_id.id,
            "partner_id": self.partner_id.id,
            "ref": self.name,
            "auto_post": "no",
            "line_ids": [
                Command.create({
                    "name": self.name,
                    "account_id": self.recognition_account_id.id,
                    "partner_id": self.partner_id.id,
                    "currency_id": self.currency_id.id,
                    "balance": self.recognition_balance,
                    "amount_currency": self.recognition_amount_currency,
                    "analytic_distribution": (
                        self.analytic_distribution or False
                    ),
                }),
                Command.create({
                    "name": self.name,
                    "account_id": deferral.deferral_account_id.id,
                    "partner_id": self.partner_id.id,
                    "currency_id": self.currency_id.id,
                    "balance": self.deferral_balance,
                    "amount_currency": self.deferral_amount_currency,
                }),
            ],
        }

    def action_post(self):
        self.check_access("write")
        for line in self:
            if line.move_id:
                if line.move_id.state != "posted":
                    line.move_id.action_post()
                line.state = "posted"
                continue
            if line.deferral_id.state == "draft":
                line.deferral_id.action_start()
            move = (
                self.env["account.move"]
                .with_company(line.company_id)
                .create(line._move_values())
            )
            move.action_post()
            line.write({
                "move_id": move.id,
                "state": "posted",
            })
        self.deferral_id._sync_state()
        return True

    def action_open_move(self):
        self.ensure_one()
        if not self.move_id:
            return False
        return {
            "type": "ir.actions.act_window",
            "name": _("Deferred Journal Entry"),
            "res_model": "account.move",
            "view_mode": "form",
            "res_id": self.move_id.id,
            "context": {"create": False, "delete": False},
        }
