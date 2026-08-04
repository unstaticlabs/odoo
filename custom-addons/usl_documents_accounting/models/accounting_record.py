from odoo import models


class RebuildAccountDeclaration(models.Model):
    _name = "rebuild.account.declaration"
    _inherit = ["rebuild.account.declaration", "usl.document.link.mixin"]


class RebuildAccountClosingPeriod(models.Model):
    _name = "rebuild.account.closing.period"
    _inherit = ["rebuild.account.closing.period", "usl.document.link.mixin"]


class UslDocumentLink(models.Model):
    _inherit = "usl.document.link"

    def _allowed_models(self):
        return super()._allowed_models() | {
            "rebuild.account.declaration",
            "rebuild.account.closing.period",
        }
