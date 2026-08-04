from odoo import models


class PlatformBillingPlatform(models.Model):
    _name = "usl.platform.billing.platform"
    _inherit = [
        "usl.platform.billing.platform",
        "usl.accounting.restore.source.mixin",
    ]


class PlatformBillingSession(models.Model):
    _name = "usl.platform.billing.session"
    _inherit = [
        "usl.platform.billing.session",
        "usl.accounting.restore.source.mixin",
    ]


class PlatformBillingPayout(models.Model):
    _name = "usl.platform.billing.payout"
    _inherit = [
        "usl.platform.billing.payout",
        "usl.accounting.restore.source.mixin",
    ]
