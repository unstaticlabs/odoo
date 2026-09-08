"""Which VAT treatment a B2C sale is taxed under, and where that VAT goes.

Goods dispatched from a Member State the shop is not established in owe the
destination's VAT from the first euro, so the liability exists whether or not
the shop has registered to remit it.  Until it has, that VAT is a liability to
be regularised, not one collected under a scheme, and saying otherwise in the
ledger would claim a registration that does not exist.

Outside the Union nothing is owed, but "nothing is owed" is the answer to more
than one question: a consumer buying goods and a business buying services are
untaxed for entirely different reasons.  A position built for one of them
cannot be told from the other by anything the customer record holds, so which
one applies is stated here rather than guessed at.
"""

from odoo import fields, models
from odoo.exceptions import UserError


class ResCompany(models.Model):
    _inherit = "res.company"

    usl_b2c_oss_registered = fields.Boolean(
        string="Registered for the One Stop Shop",
        help="Until this is true, the VAT other Member States are owed accrues "
             "to the account below instead of being declared as collected.",
    )
    usl_b2c_pending_vat_account_id = fields.Many2one(
        "account.account",
        string="Destination VAT to regularise",
        check_company=True,
        ondelete="restrict",
        help="Where VAT owed to another Member State accrues while the shop is "
             "not registered to remit it.",
    )
    usl_b2c_oss_vat_account_id = fields.Many2one(
        "account.account",
        string="Destination VAT collected",
        check_company=True,
        ondelete="restrict",
        help="Where VAT owed to another Member State goes once the shop is "
             "registered to remit it.",
    )
    usl_b2c_export_position_id = fields.Many2one(
        "account.fiscal.position",
        string="Position for B2C sales outside the Union",
        check_company=True,
        ondelete="restrict",
        help="A sale outside the Union bears no VAT, but so does a business "
             "buying services there, for a different reason. Naming the "
             "position for consumer goods keeps the two apart on the invoice. "
             "Leave it empty to let the fiscal positions decide.",
    )


class B2cImportBatchDestinationVat(models.Model):
    _inherit = "b2c.import.batch"

    def _destination_taxes(self):
        """Return the taxes another Member State's rate is charged through."""
        self.ensure_one()
        home = self.company_id.account_fiscal_country_id
        positions = self.env["account.fiscal.position"].search(
            [
                ("company_id", "=", self.company_id.id),
                ("country_id", "!=", False),
                ("country_id", "!=", home.id),
            ],
        )
        union = self.env.ref("base.europe").country_ids
        return positions.filtered(lambda item: item.country_id in union).tax_ids

    def _destination_vat_account(self):
        """Return where destination VAT belongs given what the shop has registered."""
        self.ensure_one()
        company = self.company_id
        wanted = (
            company.usl_b2c_oss_vat_account_id
            if company.usl_b2c_oss_registered
            else company.usl_b2c_pending_vat_account_id
        )
        if not wanted:
            raise UserError(
                self.env._(
                    "The company does not say where VAT owed to another Member "
                    "State goes. Name the account it accrues to.",
                ),
            )
        return wanted

    def _check_destination_vat(self):
        """Report destination VAT that would be declared under an absent scheme."""
        self.ensure_one()
        company = self.company_id
        if not (company.usl_b2c_pending_vat_account_id or company.usl_b2c_oss_vat_account_id):
            return
        wanted = self._destination_vat_account()
        misplaced = self._destination_taxes().filtered(
            lambda tax: any(
                line.account_id and line.account_id != wanted
                for line in tax.repartition_line_ids.filtered(
                    lambda item: item.repartition_type == "tax",
                )
            ),
        )
        if not misplaced:
            return
        self._raise_issue(
            "destination_vat_misplaced",
            self.env._(
                "%(count)s destination rate(s) do not accrue to %(account)s",
                count=len(misplaced),
                account=wanted.display_name,
            ),
            note=self.env._(
                "%(taxes)s post elsewhere. While the shop is %(state)s, that is "
                "where the VAT other Member States are owed belongs.",
                taxes=", ".join(misplaced.mapped("name")[:8]),
                state=(
                    self.env._("registered for the One Stop Shop")
                    if company.usl_b2c_oss_registered
                    else self.env._("not registered for the One Stop Shop")
                ),
            ),
        )

    def action_park_destination_vat(self):
        """Point every destination rate at the account the shop's status implies."""
        self.ensure_one()
        wanted = self._destination_vat_account()
        taxes = self._destination_taxes()
        for tax in taxes:
            for line in tax.repartition_line_ids.filtered(
                lambda item: item.repartition_type == "tax",
            ):
                line.account_id = wanted
        self.action_check_readiness()
        return {
            "type": "ir.actions.act_window",
            "name": self.env._("Destination rates"),
            "res_model": "account.tax",
            "view_mode": "list,form",
            "domain": [("id", "in", taxes.ids)],
        }
