from odoo import Command, api, fields, models
from odoo.exceptions import UserError


class L10nFrFecExportWizard(models.TransientModel):
    _inherit = "l10n_fr.fec.export.wizard"

    rebuild_can_generate_official_fec = fields.Boolean(
        compute="_compute_rebuild_can_generate_official_fec",
    )

    def _rebuild_can_generate_official_fec(self):
        return self.env.user.has_group("account.group_account_manager")

    @api.depends_context("uid")
    def _compute_rebuild_can_generate_official_fec(self):
        allowed = self._rebuild_can_generate_official_fec()
        for wizard in self:
            wizard.rebuild_can_generate_official_fec = allowed

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        fiscal_dates = self.env.company.compute_fiscalyear_dates(
            fields.Date.context_today(self),
        )
        fiscal_start = fiscal_dates["date_from"]
        fiscal_end = fiscal_dates["date_to"]
        if "date_from" in fields_list and not values.get("date_from"):
            values["date_from"] = fiscal_start
        if "date_to" in fields_list and not values.get("date_to"):
            values["date_to"] = fiscal_end
        if not self._rebuild_can_generate_official_fec():
            values["test_file"] = True
            values["export_type"] = "official"
        return values

    @api.model_create_multi
    def create(self, vals_list):
        if not self._rebuild_can_generate_official_fec():
            for values in vals_list:
                values["test_file"] = True
                values["export_type"] = "official"
                values["excluded_journal_ids"] = [Command.clear()]
        else:
            for values in vals_list:
                if values.get("test_file") is False:
                    values["export_type"] = "official"
                    values["excluded_journal_ids"] = [Command.clear()]
        return super().create(vals_list)

    def write(self, values):
        if not self._rebuild_can_generate_official_fec():
            if values.get("test_file") is False:
                raise UserError(
                    self.env._(
                        "Only Accounting Managers can generate a final FEC "
                        "that may update lock dates.",
                    ),
                )
            values = dict(values)
            values["test_file"] = True
            values["export_type"] = "official"
            values["excluded_journal_ids"] = [Command.clear()]
        elif values.get("test_file") is False:
            values = dict(values)
            values["export_type"] = "official"
            values["excluded_journal_ids"] = [Command.clear()]
        return super().write(values)

    def generate_fec(self):
        for wizard in self:
            if (
                not wizard._rebuild_can_generate_official_fec()
                and (
                    not wizard.test_file
                    or wizard.export_type != "official"
                    or wizard.excluded_journal_ids
                )
            ):
                raise UserError(
                    self.env._(
                        "Accountant reviewers and finance operators must "
                        "generate a complete posted-entries FEC in test mode.",
                    ),
                )
            if (
                not wizard.test_file
                and (
                    wizard.export_type != "official"
                    or wizard.excluded_journal_ids
                )
            ):
                raise UserError(
                    self.env._(
                        "An official FEC must include all posted journals.",
                    ),
                )
        return super().generate_fec()
