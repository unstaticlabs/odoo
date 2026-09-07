"""What the chart has to say before a B2C sale can state a number.

Every correction here answers a defect found on a real chart of accounts, and
every one of those defects was silent: the sale posts, the invoice prints, and
the figure on it is wrong.  A correction states the fact it depends on, does
nothing at all when that fact does not hold, and applying it again does
nothing a second time.
"""

import logging

from odoo import Command, models

_logger = logging.getLogger(__name__)


class ResCompany(models.Model):
    _inherit = "res.company"

    # -- the rate a sale is charged at -------------------------------------

    def _usl_b2c_standard_sale_tax(self, is_service):
        """Return the one rate this company charges on goods, or on services.

        A chart stating one rate for both leaves nothing to choose.  One
        stating two at the same rate tells them apart by what already carries
        each.  Where even that is silent the choice belongs to a person, and
        this returns nothing rather than guess at it.
        """
        self.ensure_one()
        taxes = self.env["account.tax"].search(
            [
                ("company_id", "=", self.id),
                ("type_tax_use", "=", "sale"),
                ("country_id", "=", self.account_fiscal_country_id.id),
                ("amount_type", "=", "percent"),
                ("amount", ">", 0),
            ],
        )
        highest = max(taxes.mapped("amount"), default=0)
        standard = taxes.filtered(lambda tax: tax.amount == highest)
        if len(standard) < 2:
            return standard[:1]
        for_services = standard.filtered(
            lambda tax: self.env["product.template"].search_count(
                [("taxes_id", "in", tax.id), ("type", "=", "service")], limit=1,
            ),
        )
        wanted = for_services if is_service else standard - for_services
        return wanted if len(wanted) == 1 else self.env["account.tax"]

    def _usl_b2c_channel_sale_taxes(self):
        """Return the rates a channel sale of this company can be stated at."""
        self.ensure_one()
        positions = self.env["account.fiscal.position"].search(
            [
                ("company_id", "=", self.id),
                ("auto_apply", "=", True),
                "|",
                ("country_id", "!=", False),
                ("country_group_id", "!=", False),
            ],
        )
        positions |= self.usl_b2c_export_position_id
        destinations = positions.tax_ids.filtered(
            lambda tax: tax.active and tax.type_tax_use == "sale",
        )
        return destinations | self._usl_b2c_standard_sale_tax(is_service=False)

    # -- the corrections ---------------------------------------------------

    def _usl_b2c_retire_shadowing_positions(self):
        """Retire a position answering for a country built to answer itself.

        A position naming no country, ranked above the ones that name one,
        takes every destination — which is how a German sale comes to be
        invoiced without German VAT.  Only a position naming no country is
        ever retired, only where a country with one of its own is being
        answered for, and never one a partner has been pinned to: that is
        somebody's decision and not a defect.
        """
        self.ensure_one()
        Position = self.env["account.fiscal.position"].with_company(self)
        applying = Position.search(
            [("company_id", "=", self.id), ("auto_apply", "=", True)],
        )
        shadowing = Position.browse()
        for country in applying.country_id:
            partner = self.env["res.partner"].new(
                {"name": "chart", "country_id": country.id},
            )
            answering = Position._get_fiscal_position(partner)
            if not answering or answering.country_id or answering.country_group_id:
                continue
            if answering == self.usl_b2c_export_position_id:
                continue
            shadowing |= answering
        for position in shadowing:
            pinned = self.env["res.partner"].with_company(self).with_context(
                active_test=False,
            ).search_count([("property_account_position_id", "=", position.id)], limit=1)
            if pinned:
                _logger.info(
                    "%s answers for countries with their own position but is pinned "
                    "to a partner, so it is reported and not retired.",
                    position.display_name,
                )
                continue
            position.active = False
            _logger.info("Retired the shadowing fiscal position %s.", position.display_name)

    def _usl_b2c_state_prices_tax_included(self):
        """State the rates a channel sale is charged at as inside the price.

        What a marketplace reports is what the customer paid, so the tax is
        already in it.  Only a rate nothing has ever been posted at is
        changed: one the books have used means something by being stated
        outside the price, and this cannot know what.
        """
        self.ensure_one()
        Line = self.env["account.move.line"]
        candidates = self._usl_b2c_channel_sale_taxes().filtered(
            lambda tax: tax.price_include_override != "tax_included",
        )
        included = candidates.filtered(
            lambda tax: not Line.search_count(
                ["|", ("tax_ids", "in", tax.id), ("tax_line_id", "=", tax.id)], limit=1,
            ),
        )
        if included:
            included.price_include_override = "tax_included"
            _logger.info(
                "Stated %s unused sales rate(s) as included in the price: %s.",
                len(included),
                ", ".join(included.mapped("name")),
            )
        for tax in candidates - included:
            _logger.info(
                "%s has been posted at and is left stated outside the price.",
                tax.display_name,
            )

    def _usl_b2c_settle_product_taxes(self):
        """Give each catalog product the one rate its kind of thing is charged at.

        A product carrying the goods rate and the services rate at once is a
        product an invoice would charge twice.
        """
        self.ensure_one()
        goods = self._usl_b2c_standard_sale_tax(is_service=False)
        services = self._usl_b2c_standard_sale_tax(is_service=True)
        if not goods or not services:
            _logger.info(
                "The chart does not tell a goods rate from a services rate, so no "
                "product's tax is settled.",
            )
            return
        templates = self.env["product.template"].with_company(self).with_context(
            active_test=False,
        ).search([("b2c_catalog_classification", "=", "operational")])
        settled = self.env["product.template"]
        for template in templates:
            wanted = services if template.type == "service" else goods
            stated = template.taxes_id.filtered(
                lambda tax: tax.company_id == self and tax.type_tax_use == "sale",
            )
            if stated == wanted:
                continue
            # Other companies state their own rates on the same product, and
            # what they say is not this company's to settle.
            template.taxes_id = [
                Command.set(((template.taxes_id - stated) | wanted).ids),
            ]
            settled |= template
        if settled:
            _logger.info("Gave %s catalog product(s) one sales rate.", len(settled))

    def _usl_b2c_account_for_revenue(self):
        """Say where the revenue of each kind of B2C good goes.

        Goods made in house and goods bought to resell do not share an
        account, and carriage charged to a customer is neither.  A category
        already naming an account has been decided and is left alone; which
        category a product belongs to is not settled here.
        """
        self.ensure_one()
        wanted = (
            ("usl_b2c.product_category_gbc_finished_products", None, "701000"),
            ("usl_b2c.product_category_gbc_print_on_demand", None, "707000"),
            (None, "GBC Raw Materials", "707000"),
            (None, "GBC Resale Goods", "707000"),
            (None, "Deliveries", "708500"),
        )
        for reference, name, code in wanted:
            category = (
                self.env.ref(reference, raise_if_not_found=False)
                if reference
                else self.env["product.category"].search([("name", "=", name)], limit=1)
            )
            # The code of an account is what the company reading it says it
            # is, so the company has to be the one asking.
            account = self.env["account.account"].with_company(self).search(
                [("company_ids", "in", self.id), ("code", "=", code), ("active", "=", True)],
                limit=1,
            )
            if not category or not account:
                continue
            stated = category.with_company(self).property_account_income_categ_id
            if stated:
                continue
            category.with_company(self).property_account_income_categ_id = account
            _logger.info(
                "%s now states its revenue in %s.",
                category.display_name,
                account.display_name,
            )

    def _usl_b2c_reverse_charge_operators(self):
        """Let a channel operator's commission be reverse-charged.

        A commission billed from another Member State is the buyer's VAT to
        account for, and Odoo only accounts for it when a position requiring a
        tax number answers for the supplier.  Where the number has never been
        checked against VIES no position does, the one built for *selling* to
        that country answers instead, and no reverse charge is made at all.
        """
        self.ensure_one()
        Position = self.env["account.fiscal.position"].with_company(self)
        intra = Position.search(
            [
                ("company_id", "=", self.id),
                ("auto_apply", "=", True),
                ("vat_required", "=", True),
            ],
            limit=1,
        )
        if not intra:
            return
        abroad = self.env.ref("base.europe").country_ids - self.account_fiscal_country_id
        operators = self.env["b2c.channel"].search(
            [("company_id", "=", self.id)],
        ).operator_partner_id
        for partner in operators.filtered(lambda item: item.country_id in abroad):
            if Position._get_fiscal_position(partner).vat_required:
                continue
            stated = partner.with_company(self)
            if stated.property_account_position_id:
                continue
            stated.property_account_position_id = intra
            _logger.info(
                "%s now states %s, so its commission is reverse-charged.",
                partner.display_name,
                intra.display_name,
            )
