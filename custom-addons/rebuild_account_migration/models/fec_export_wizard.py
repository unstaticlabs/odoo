from odoo import api, models
from odoo.exceptions import UserError


class L10nFrFecExportWizard(models.TransientModel):
    _inherit = "l10n_fr.fec.export.wizard"

    def _rebuild_can_generate_official_fec(self):
        return self.env.user.has_group("account.group_account_user")

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
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
        return super().create(vals_list)

    def write(self, values):
        if not self._rebuild_can_generate_official_fec():
            if values.get("test_file") is False:
                raise UserError(self.env._("Only full accounting users can generate a final FEC that may update lock dates."))
            values = dict(values)
            values["test_file"] = True
            values["export_type"] = "official"
        return super().write(values)

    def generate_fec(self):
        for wizard in self:
            if not wizard._rebuild_can_generate_official_fec() and not wizard.test_file:
                raise UserError(self.env._("Only full accounting users can generate a final FEC that may update lock dates."))
        return super().generate_fec()
