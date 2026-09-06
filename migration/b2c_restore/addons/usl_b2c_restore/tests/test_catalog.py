"""The reviewed catalog must cover the history without inventing anything."""

from odoo.tests import BaseCase, tagged

from odoo.addons.usl_b2c_restore import private_evidence


@tagged("post_install", "-at_install")
class TestReviewedCatalog(BaseCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.available = private_evidence.available(
            "catalog-specification-2026-09-06.json",
        )

    def catalog(self):
        if not self.available:
            self.skipTest("The pinned catalog specification is not mounted here.")
        return private_evidence.catalog_specification()

    def test_every_variant_answers_the_same_attributes(self):
        """Odoo builds variants from a matrix, so the shape cannot vary."""
        for product in self.catalog():
            shapes = {
                frozenset(variant["attributes"]) for variant in product["variants"]
            }
            self.assertEqual(
                len(shapes), 1,
                f"{product['key']} mixes attribute shapes: {shapes!r}",
            )

    def test_no_source_identity_is_claimed_twice(self):
        seen = {}
        for product in self.catalog():
            for variant in product["variants"]:
                for alias in variant["aliases"]:
                    identity = (
                        alias["provider"], alias["sku"],
                        alias["name"], alias["variation"],
                    )
                    self.assertNotIn(
                        identity, seen,
                        f"{identity!r} is claimed by {seen.get(identity)} "
                        f"and {product['key']}",
                    )
                    seen[identity] = product["key"]
        self.assertEqual(len(seen), 184)

    def test_own_stock_products_are_the_ones_that_consume_inventory(self):
        own = {
            product["key"] for product in self.catalog()
            if product["fulfilment_mode"] == "own_stock"
        }
        self.assertEqual(
            own,
            {
                "chain-heavy-6mm", "chains-ankle", "chains-wrist",
                "collar-everyday", "padlock-40mm", "padlock-master-20mm",
            },
        )

    def test_a_variant_always_names_at_least_one_source_identity(self):
        for product in self.catalog():
            for variant in product["variants"]:
                self.assertTrue(
                    variant["aliases"],
                    f"{product['key']} has a variant nothing was ever sold as",
                )
