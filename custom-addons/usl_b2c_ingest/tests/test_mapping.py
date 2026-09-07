"""Deciding which product a channel sold."""

from odoo.exceptions import UserError
from odoo.tests import tagged

from . import fixtures
from .test_import_batch import TestImportBatch


@tagged("post_install", "-at_install", "usl_b2c_ingest")
class TestMapping(TestImportBatch):
    """Lookups map, exact matches derive, and anything else names what it needs."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        attribute = cls.env["product.attribute"].create({"name": "Invented Colour"})
        cls.colours = cls.env["product.attribute.value"].create(
            [{"name": name, "attribute_id": attribute.id} for name in ("Black", "White")],
        )
        cls.template = cls.env["product.template"].create(
            {
                "name": "Invented Jersey",
                "attribute_line_ids": [
                    (0, 0, {"attribute_id": attribute.id, "value_ids": [(6, 0, cls.colours.ids)]}),
                ],
            },
        )
        cls.attribute_line = cls.template.attribute_line_ids

    def _alias(self, channel, product, **values):
        return self.env["b2c.product.alias"].create(
            {
                "company_id": self.company.id,
                "channel_id": self.channels[channel].id,
                "source_provider": channel,
                "product_id": product.id,
                "mapping_state": "verified",
            }
            | values,
        )

    def _variant(self, colour):
        return self.template.product_variant_ids.filtered(
            lambda product, name=colour: name
            in product.product_template_attribute_value_ids.mapped("name"),
        )

    def _resolved(self):
        batch = self._batch(
            **{
                "etsy-orders.csv": fixtures.etsy_orders(),
                "etsy-items.csv": fixtures.etsy_order_items(),
            },
        )
        batch.action_parse()
        batch.action_resolve()
        return batch

    def _line(self, batch, transaction_id):
        return batch.row_ids.filtered(
            lambda row: row.external_line_id == transaction_id,
        )

    def test_a_confirmed_alias_maps_without_being_asked(self):
        self._alias(
            "etsy",
            self._variant("Black"),
            original_sku="FICTION-JERSEY-BLACK-M",
            original_name="Invented Jersey",
            original_variation="Color:Black,Size:M",
        )
        batch = self._resolved()
        line = self._line(batch, "7000000001")
        self.assertEqual(line.mapping, "mapped")
        self.assertEqual(line.product_id, self._variant("Black"))

    def test_a_channel_sku_that_names_one_product_is_enough(self):
        self._alias(
            "etsy",
            self._variant("Black"),
            original_sku="FICTION-JERSEY-BLACK-M",
            original_name="Invented Jersey",
            original_variation="Color:Black,Size:M — an older listing",
        )
        batch = self._resolved()
        self.assertEqual(self._line(batch, "7000000001").mapping, "mapped")

    def test_a_new_variant_of_a_known_product_is_derived(self):
        self._alias(
            "etsy",
            self._variant("Black"),
            original_sku="FICTION-JERSEY-BLACK-M",
            original_name="Invented Cap",
            original_variation="Color:White,Size:M",
        )
        batch = self._resolved()
        line = self._line(batch, "7000000002")
        self.assertEqual(line.mapping, "derived")
        self.assertEqual(line.product_id, self._variant("White"))
        self.assertIn("alias_derived", {issue.kind for issue in batch.issue_ids})

    def test_confirming_a_derived_mapping_writes_a_verified_alias(self):
        self._alias(
            "etsy",
            self._variant("Black"),
            original_sku="FICTION-JERSEY-BLACK-M",
            original_name="Invented Cap",
            original_variation="Color:White,Size:M",
        )
        batch = self._resolved()
        action = batch.action_confirm_mappings()
        written = self.env["b2c.product.alias"].browse(action["domain"][0][2])
        self.assertEqual(len(written), 1)
        self.assertEqual(written.mapping_state, "verified")
        self.assertEqual(written.product_id, self._variant("White"))
        self.assertEqual(self._line(batch, "7000000002").mapping, "mapped")

    def test_confirming_twice_writes_the_alias_once(self):
        self._alias(
            "etsy",
            self._variant("Black"),
            original_sku="FICTION-JERSEY-BLACK-M",
            original_name="Invented Cap",
            original_variation="Color:White,Size:M",
        )
        batch = self._resolved()
        batch.action_confirm_mappings()
        batch.action_resolve()
        action = batch.action_confirm_mappings()
        self.assertFalse(action["domain"][0][2])

    def test_a_product_odoo_does_not_know_is_named_not_guessed(self):
        batch = self._resolved()
        kinds = {issue.kind for issue in batch.issue_ids}
        self.assertIn("unknown_product", kinds)
        self.assertEqual(batch.unmapped_line_count, 2)
        self.assertFalse(self._line(batch, "7000000001").product_id)

    def _colour_not_in_the_catalogue(self):
        """Return a drop whose second item states a colour the template lacks."""
        self._alias(
            "etsy",
            self._variant("Black"),
            original_sku="FICTION-JERSEY-BLACK-M",
            original_name="Invented Cap",
            original_variation="Color:White",
        )
        rows = [
            dict(fixtures.ETSY_ITEM_ROWS[0]),
            dict(fixtures.ETSY_ITEM_ROWS[1]) | {"Variations": "Color:Emerald"},
        ]
        batch = self._batch(
            **{
                "etsy-orders.csv": fixtures.etsy_orders(),
                "etsy-items.csv": fixtures.etsy_order_items(rows),
            },
        )
        batch.action_parse()
        batch.action_resolve()
        return batch

    def test_a_missing_attribute_value_is_named_with_what_the_attribute_offers(self):
        batch = self._colour_not_in_the_catalogue()
        issue = batch.issue_ids.filtered(lambda item: item.kind == "missing_variant")
        self.assertTrue(issue)
        self.assertIn("Invented Colour", issue.note)
        self.assertIn("Black, White", issue.note)
        self.assertEqual(issue.proposed_value, "Emerald")

    def test_adding_the_named_value_maps_the_line(self):
        batch = self._colour_not_in_the_catalogue()
        issue = batch.issue_ids.filtered(lambda item: item.kind == "missing_variant")
        issue.action_add_missing_value()
        line = self._line(batch, "7000000002")
        self.assertEqual(line.mapping, "derived")
        self.assertEqual(
            line.product_id.product_template_attribute_value_ids.mapped("name"),
            ["Emerald"],
        )
        self.assertFalse(batch.unmapped_line_count)

    def test_a_value_that_is_not_named_cannot_be_added(self):
        batch = self._colour_not_in_the_catalogue()
        issue = batch.issue_ids.filtered(lambda item: item.kind == "missing_variant")
        issue.proposed_value = ""
        with self.assertRaises(UserError):
            issue.action_add_missing_value()

    def test_resolving_again_replaces_the_findings_it_made(self):
        batch = self._resolved()
        before = len(batch.issue_ids)
        batch.action_resolve()
        self.assertEqual(len(batch.issue_ids), before)

    def test_a_name_that_points_at_two_products_is_not_chosen_between(self):
        other = self.env["product.template"].create({"name": "Another Invented Jersey"})
        self._alias(
            "etsy",
            self._variant("Black"),
            original_sku="A",
            original_name="Invented Cap",
            original_variation="Color:White,Size:M",
        )
        self._alias(
            "etsy",
            other.product_variant_ids,
            original_sku="B",
            original_name="Invented Cap",
            original_variation="Color:Black,Size:L",
        )
        batch = self._resolved()
        self.assertIn("ambiguous_product", {issue.kind for issue in batch.issue_ids})
        self.assertEqual(self._line(batch, "7000000002").mapping, "unmapped")

    def test_resolving_needs_the_files_read_first(self):
        batch = self._batch(**{"etsy-orders.csv": fixtures.etsy_orders()})
        with self.assertRaises(Exception):
            batch.action_resolve()
