"""Everything an ingest depends on, in one place a person can find.

The configuration was reachable only from the company record and the Technical
menu, which is where a setting goes to be forgotten.  Nothing here is new: it
is the same fields and the same system parameter, gathered under the B2C
section of Settings so that setting the shop up is one page rather than a
scavenger hunt.
"""

from odoo import fields, models

from odoo.addons.usl_b2c_ingest.services.printful import TOKEN_PARAMETER


class ResConfigSettingsB2cIngest(models.TransientModel):
    _inherit = "res.config.settings"

    # -- the supplier ------------------------------------------------------

    usl_b2c_printful_token = fields.Char(
        string="Printful token",
        config_parameter=TOKEN_PARAMETER,
        groups="base.group_system",
        help="A read-only Printful API token. It is what lets a drop ask the "
             "supplier what each order cost and when it shipped.",
    )
    usl_b2c_supply_partner_id = fields.Many2one(
        related="company_id.usl_b2c_supply_partner_id",
        readonly=False,
    )
    usl_b2c_wallet_journal_id = fields.Many2one(
        related="company_id.usl_b2c_wallet_journal_id",
        readonly=False,
    )
    usl_b2c_supply_product_id = fields.Many2one(
        related="company_id.usl_b2c_supply_product_id",
        readonly=False,
    )
    usl_b2c_internal_supply_product_id = fields.Many2one(
        related="company_id.usl_b2c_internal_supply_product_id",
        readonly=False,
    )

    # -- where the money meets the bank ------------------------------------

    usl_b2c_bank_journal_id = fields.Many2one(
        related="company_id.usl_b2c_bank_journal_id",
        readonly=False,
    )
    usl_b2c_transfer_journal_id = fields.Many2one(
        related="company_id.usl_b2c_transfer_journal_id",
        readonly=False,
    )

    # -- what a sale owes --------------------------------------------------

    usl_b2c_oss_registered = fields.Boolean(
        related="company_id.usl_b2c_oss_registered",
        readonly=False,
    )
    usl_b2c_pending_vat_account_id = fields.Many2one(
        related="company_id.usl_b2c_pending_vat_account_id",
        readonly=False,
    )
    usl_b2c_oss_vat_account_id = fields.Many2one(
        related="company_id.usl_b2c_oss_vat_account_id",
        readonly=False,
    )
    usl_b2c_export_position_id = fields.Many2one(
        related="company_id.usl_b2c_export_position_id",
        readonly=False,
    )
