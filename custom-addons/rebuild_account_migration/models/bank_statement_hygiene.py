from odoo import api, fields, models


class RebuildAccountHygieneIssue(models.Model):
    _inherit = "rebuild.account.hygiene.issue"

    @api.model
    def _evaluate_builtin_hygiene(self, company):
        candidates = super()._evaluate_builtin_hygiene(company)
        today = fields.Date.context_today(self)
        configs = self.env["account.bank.ingestion.config"].search(
            [("company_id", "=", company.id), ("active", "=", True)]
        )
        for config in configs:
            statement = config.expected_statement_id
            overdue = config.review_status == "expected" and today > config.expected_delivery_date
            if config.review_status not in ("attention", "reopened") and not overdue:
                continue
            record = statement or config.journal_id
            period = config.expected_period_start.strftime("%B %Y")
            if overdue:
                title = f"Receive the {period} bank export"
                description = "The scheduled bank export has not been received by its expected delivery date."
                next_action = "Confirm the scheduled recipient in Shine and the Odoo bank-export alias, then recover the export if needed."
                issue_type = "evidence"
                severity = "2_warning"
                evidence = f"Expected on {config.expected_delivery_date}."
                amount = 0
            else:
                title = f"Review the {period} bank statement"
                description = config.review_next_action
                next_action = "Open the monthly bank statement, resolve the displayed discrepancy or source exception, then certify it."
                issue_type = "technical" if statement and statement.unresolved_exception_count else "evidence"
                severity = "1_blocking"
                evidence = (
                    f"{statement.unresolved_exception_count} open source exception(s); "
                    f"balance difference {statement.balance_difference:.2f}."
                    if statement
                    else "A received export needs accounting review."
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
                    "The bank statement is the monthly evidence that Odoo contains the complete bank movement population.",
                    next_action,
                    "This is a bank-completeness checkpoint; it does not imply that every movement is matched to an invoice or payment.",
                    evidence,
                    record,
                    amount=amount,
                    owner_role="accountant_reviewer",
                    issue_date=config.expected_period_end,
                )
            )
        return candidates


class RebuildAccountOverview(models.Model):
    _inherit = "rebuild.account.overview"

    bank_checkpoint_config_id = fields.Many2one(
        "account.bank.ingestion.config", compute="_compute_bank_checkpoint"
    )
    bank_checkpoint_status = fields.Selection(
        selection=lambda self: self.env["account.bank.ingestion.config"]._fields[
            "review_status"
        ].selection,
        compute="_compute_bank_checkpoint",
    )
    bank_checkpoint_period = fields.Char(compute="_compute_bank_checkpoint")
    bank_checkpoint_next_action = fields.Char(compute="_compute_bank_checkpoint")

    @api.depends("company_id")
    def _compute_bank_checkpoint(self):
        Config = self.env["account.bank.ingestion.config"]
        for overview in self:
            configs = Config.search(
                [("company_id", "=", overview.company_id.id), ("active", "=", True)]
            )
            config = configs.sorted(
                lambda item: (item.expected_period_start or fields.Date.today(), item.id)
            )[:1]
            overview.bank_checkpoint_config_id = config
            overview.bank_checkpoint_status = config.review_status if config else False
            overview.bank_checkpoint_period = (
                f"{config.journal_id.display_name} — {config.expected_period_start:%B %Y}"
                if config and config.expected_period_start
                else False
            )
            overview.bank_checkpoint_next_action = config.review_next_action if config else False

    def action_open_bank_checkpoint(self):
        self.ensure_one()
        if not self.bank_checkpoint_config_id:
            return self.env["ir.actions.actions"]._for_xml_id(
                "account.open_account_journal_dashboard_kanban"
            )
        return self.bank_checkpoint_config_id.action_open_expected_statement()
