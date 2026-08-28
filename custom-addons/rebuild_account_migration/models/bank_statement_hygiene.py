from odoo import _, api, fields, models
from odoo.tools import format_date


class RebuildAccountHygieneIssue(models.Model):
    _inherit = "rebuild.account.hygiene.issue"

    @api.model
    def _evaluate_builtin_hygiene(self, company):
        candidates = super()._evaluate_builtin_hygiene(company)
        today = fields.Date.context_today(self)
        configs = self.env["account.bank.ingestion.config"].search(
            [("company_id", "=", company.id), ("active", "=", True)],
        )
        for config in configs:
            statement = config.expected_statement_id
            overdue = (
                config.review_status == "expected"
                and today > config.expected_delivery_date
            )
            if config.review_status not in ("attention", "reopened") and not overdue:
                continue
            record = statement or config.journal_id
            period = format_date(
                self.env,
                config.expected_period_start,
                date_format="MMMM y",
            )
            if overdue:
                title = _("Receive the %(period)s bank export", period=period)
                description = _(
                    "The scheduled bank export has not been received by its expected delivery date.",
                )
                next_action = _(
                    "Confirm the scheduled recipient in Shine and the Odoo bank-export address, then recover the export if needed.",
                )
                issue_type = "evidence"
                severity = "2_warning"
                evidence = _(
                    "Expected on %(date)s.",
                    date=format_date(self.env, config.expected_delivery_date),
                )
                amount = 0
            else:
                title = _("Review the %(period)s bank statement", period=period)
                description = config.review_next_action
                next_action = _(
                    "Open the monthly bank statement, resolve the displayed discrepancy or source exception, then certify it.",
                )
                issue_type = (
                    "technical"
                    if statement and statement.unresolved_exception_count
                    else "evidence"
                )
                severity = "1_blocking"
                evidence = (
                    _(
                        "%(count)s open source exception(s); balance difference %(difference).2f.",
                        count=statement.unresolved_exception_count,
                        difference=statement.balance_difference,
                    )
                    if statement
                    else _("A received export needs accounting review.")
                )
                amount = statement.balance_difference if statement else 0
            candidates.append(
                self._issue_values(
                    company,
                    f"bank-statement:{config.id}:{config.expected_period_start}",
                    "hygiene_bank_statement",
                    issue_type,
                    severity,
                    title,
                    description,
                    _(
                        "The bank statement is the monthly evidence that Odoo contains the complete bank movement population.",
                    ),
                    next_action,
                    _(
                        "This is a bank-completeness checkpoint; it does not imply that every movement is matched to an invoice or payment.",
                    ),
                    evidence,
                    record,
                    amount=amount,
                    owner_role="accountant_reviewer",
                    issue_date=config.expected_period_end,
                ),
            )
        return candidates


class RebuildAccountOverview(models.Model):
    _inherit = "rebuild.account.overview"

    bank_checkpoint_config_id = fields.Many2one(
        "account.bank.ingestion.config",
        compute="_compute_bank_checkpoint",
    )
    bank_checkpoint_status = fields.Selection(
        selection=lambda self: (
            self.env["account.bank.ingestion.config"]._fields["review_status"].selection
        ),
        compute="_compute_bank_checkpoint",
    )
    bank_checkpoint_period = fields.Char(compute="_compute_bank_checkpoint")
    bank_checkpoint_next_action = fields.Char(compute="_compute_bank_checkpoint")

    @api.depends("company_id")
    def _compute_bank_checkpoint(self):
        Config = self.env["account.bank.ingestion.config"]
        can_read_config = Config.has_access("read")
        for overview in self:
            if not can_read_config:
                # The monthly setup contains routing and sender policy reserved
                # for Accounting administrators.  Reviewers must still be able
                # to open the Accounting overview without that configuration
                # being disclosed through a computed field.
                overview.bank_checkpoint_config_id = False
                overview.bank_checkpoint_status = False
                overview.bank_checkpoint_period = False
                overview.bank_checkpoint_next_action = False
                continue
            configs = Config.search(
                [("company_id", "=", overview.company_id.id), ("active", "=", True)],
            )
            config = configs.sorted(
                lambda item: (
                    item.expected_period_start or fields.Date.today(),
                    item.id,
                ),
            )[:1]
            overview.bank_checkpoint_config_id = config
            overview.bank_checkpoint_status = config.review_status if config else False
            overview.bank_checkpoint_period = (
                f"{config.journal_id.display_name} — "
                f"{format_date(self.env, config.expected_period_start, date_format='MMMM y')}"
                if config and config.expected_period_start
                else False
            )
            overview.bank_checkpoint_next_action = (
                config.review_next_action if config else False
            )

    def action_open_bank_checkpoint(self):
        self.ensure_one()
        if not self.bank_checkpoint_config_id:
            return self.env["ir.actions.actions"]._for_xml_id(
                "account.open_account_journal_dashboard_kanban",
            )
        return self.bank_checkpoint_config_id.action_open_expected_statement()
