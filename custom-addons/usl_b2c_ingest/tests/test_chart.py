"""Correcting the chart the numbers depend on.

Each test builds one of the defects found on a real chart of accounts and
proves the correction settles it — and, just as importantly, that it leaves
alone what it has no business touching.
"""

from odoo.tests import tagged

from .test_materialise import TestMaterialise


@tagged("post_install", "-at_install", "usl_b2c_ingest")
class TestChart(TestMaterialise):
    """A correction states the fact it depends on and does nothing without it."""

    def _batch(self):
        return self.env["b2c.import.batch"].create(
            {"name": "Chart", "company_id": self.company.id},
        )

    # -- a position answering for a country that has its own ---------------

    def test_a_position_answering_for_everyone_is_retired(self):
        shadow = self._position(
            "Invented catch-all", False, self.home_tax, sequence=1,
        )
        partner = self.env["res.partner"].new(
            {"name": "buyer", "country_id": self.germany.id},
        )
        answering = self.env["account.fiscal.position"]._get_fiscal_position(partner)
        self.assertEqual(answering, shadow, "the defect is not set up")

        self.company._usl_b2c_retire_shadowing_positions()

        self.assertFalse(shadow.active)
        self.assertEqual(
            self.env["account.fiscal.position"]._get_fiscal_position(partner).name,
            "Invented Germany",
        )

    def test_a_position_of_last_resort_is_left_alone(self):
        """The export position names no country either, and answers for nobody else."""
        export = self.env["account.fiscal.position"].search(
            [("company_id", "=", self.company.id), ("name", "=", "Invented export")],
        )
        self._position("Invented catch-all", False, self.home_tax, sequence=1)

        self.company._usl_b2c_retire_shadowing_positions()

        self.assertTrue(export.active, "a position nothing is shadowed by was retired")

    def test_a_position_someone_pinned_a_partner_to_is_reported_not_retired(self):
        shadow = self._position(
            "Invented catch-all", False, self.home_tax, sequence=1,
        )
        self.env["res.partner"].create(
            {"name": "Invented pinned", "country_id": self.germany.id},
        ).with_company(self.company).property_account_position_id = shadow

        self.company._usl_b2c_retire_shadowing_positions()

        self.assertTrue(shadow.active, "somebody's decision was overruled")

    # -- what the customer paid --------------------------------------------

    def test_only_a_rate_nothing_was_posted_at_moves_into_the_price(self):
        self.home_tax.price_include_override = "tax_excluded"
        self.away_tax.price_include_override = "tax_excluded"
        self.env["account.move"].create(
            {
                "move_type": "out_invoice",
                "company_id": self.company.id,
                "partner_id": self.env["res.partner"].create({"name": "Invented buyer"}).id,
                "invoice_line_ids": [
                    (0, 0, {
                        "name": "Invented line",
                        "quantity": 1,
                        "price_unit": 100,
                        "tax_ids": [(6, 0, self.home_tax.ids)],
                    }),
                ],
            },
        )

        self.company._usl_b2c_state_prices_tax_included()

        self.assertEqual(self.home_tax.price_include_override, "tax_excluded")
        self.assertEqual(self.away_tax.price_include_override, "tax_included")

    # -- one rate per product ----------------------------------------------

    def test_a_product_ends_up_charged_one_rate(self):
        services = self._tax("Invented 20% FR services", 20, self.france)
        # The rule tells the two apart by what already carries each, so the
        # services rate must be what a service carries.
        self.carriage.taxes_id = [(6, 0, services.ids)]
        template = self._product("Invented double-taxed").product_tmpl_id
        template.write(
            {
                "b2c_catalog_classification": "operational",
                "taxes_id": [(6, 0, (self.home_tax | services).ids)],
            },
        )

        self.company._usl_b2c_settle_product_taxes()

        self.assertEqual(template.taxes_id, self.home_tax)

    def test_nothing_is_settled_when_the_chart_cannot_tell_the_rates_apart(self):
        # Two rates at the same amount, and nothing carrying either of them
        # that says which is for goods and which is for services.
        self.carriage.taxes_id = [(5, 0, 0)]
        self._tax("Invented 20% FR twin", 20, self.france)
        template = self._product("Invented ambiguous").product_tmpl_id
        stated = self.home_tax | self.away_tax
        template.write(
            {"b2c_catalog_classification": "operational", "taxes_id": [(6, 0, stated.ids)]},
        )

        self.company._usl_b2c_settle_product_taxes()

        self.assertEqual(template.taxes_id, stated, "a guess was made")

    # -- where revenue goes ------------------------------------------------

    def test_a_category_that_names_no_account_is_given_one(self):
        finished = self.env.ref("usl_b2c.product_category_gbc_finished_products")
        finished.with_company(self.company).property_account_income_categ_id = False
        account = self._numbered_account("Invented finished goods revenue", "701000")

        self.company._usl_b2c_account_for_revenue()

        self.assertEqual(
            finished.with_company(self.company).property_account_income_categ_id,
            account,
        )

    def test_a_category_that_already_names_one_is_left_alone(self):
        finished = self.env.ref("usl_b2c.product_category_gbc_finished_products")
        finished.with_company(self.company).property_account_income_categ_id = self.income
        self._numbered_account("Invented finished goods revenue", "701000")

        self.company._usl_b2c_account_for_revenue()

        self.assertEqual(
            finished.with_company(self.company).property_account_income_categ_id,
            self.income,
        )

    # -- the commission a marketplace charges ------------------------------

    def test_an_operator_abroad_is_given_the_position_that_reverse_charges(self):
        intra = self.env["account.fiscal.position"].create(
            {
                "name": "Invented intra-Community",
                "company_id": self.company.id,
                "auto_apply": True,
                "vat_required": True,
                "country_group_id": self.env.ref("base.europe").id,
                "sequence": 30,
            },
        )
        operator = self.env["res.partner"].create(
            {"name": "Invented operator", "country_id": self.env.ref("base.ie").id},
        )
        self.channels["etsy"].operator_partner_id = operator

        self.company._usl_b2c_reverse_charge_operators()

        self.assertEqual(
            operator.with_company(self.company).property_account_position_id, intra,
        )

    def test_an_operator_at_home_is_left_alone(self):
        self.env["account.fiscal.position"].create(
            {
                "name": "Invented intra-Community",
                "company_id": self.company.id,
                "auto_apply": True,
                "vat_required": True,
                "country_group_id": self.env.ref("base.europe").id,
                "sequence": 30,
            },
        )
        operator = self.env["res.partner"].create(
            {"name": "Invented home operator", "country_id": self.france.id},
        )
        self.channels["etsy"].operator_partner_id = operator

        self.company._usl_b2c_reverse_charge_operators()

        self.assertFalse(
            operator.with_company(self.company).property_account_position_id,
        )

    # -- all of it, twice --------------------------------------------------

    def test_correcting_the_chart_again_changes_nothing(self):
        self._position("Invented catch-all", False, self.home_tax, sequence=1)
        self.home_tax.price_include_override = "tax_excluded"
        batch = self._batch()

        batch.action_correct_chart()
        first = self._chart_state()
        batch.action_correct_chart()

        self.assertEqual(self._chart_state(), first)

    def _chart_state(self):
        positions = self.env["account.fiscal.position"].with_context(
            active_test=False,
        ).search([("company_id", "=", self.company.id)])
        taxes = self.env["account.tax"].search([("company_id", "=", self.company.id)])
        return (
            sorted((p.name, p.active) for p in positions),
            sorted((t.name, t.price_include_override) for t in taxes),
        )

    @classmethod
    def _numbered_account(cls, name, code):
        found = cls.env["account.account"].search(
            [("company_ids", "in", cls.company.id), ("code", "=", code)], limit=1,
        )
        return found or cls.env["account.account"].create(
            {
                "name": name,
                "code": code,
                "account_type": "income",
                "company_ids": [(6, 0, cls.company.ids)],
            },
        )
