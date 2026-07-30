from odoo import models


class PlatformBillingPlatform(models.Model):
    _name = "usl.platform.billing.platform"
    _inherit = [
        "usl.platform.billing.platform",
        "rebuild.source.trace.mixin",
    ]


class PlatformBillingSession(models.Model):
    _name = "usl.platform.billing.session"
    _inherit = [
        "usl.platform.billing.session",
        "rebuild.source.trace.mixin",
    ]


class PlatformBillingPayout(models.Model):
    _name = "usl.platform.billing.payout"
    _inherit = [
        "usl.platform.billing.payout",
        "rebuild.source.trace.mixin",
    ]
