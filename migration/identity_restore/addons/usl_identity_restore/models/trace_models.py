from odoo import models


class ResPartnerBank(models.Model):
    _name = "res.partner.bank"
    _inherit = ["res.partner.bank", "rebuild.source.trace.mixin"]


class ResPartnerCategory(models.Model):
    _name = "res.partner.category"
    _inherit = ["res.partner.category", "rebuild.source.trace.mixin"]


class ResPartnerIndustry(models.Model):
    _name = "res.partner.industry"
    _inherit = ["res.partner.industry", "rebuild.source.trace.mixin"]


class ResUsers(models.Model):
    _name = "res.users"
    _inherit = ["res.users", "rebuild.source.trace.mixin"]
