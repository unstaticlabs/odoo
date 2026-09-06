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


@tagged("post_install", "-at_install")
class TestUnrecordedAttributes(BaseCase):
    """A product must never reach Odoo carrying an unallocated attribute."""

    def resolve(self, products):
        from odoo.addons.usl_b2c_restore.models.catalog import resolve_unrecorded

        return resolve_unrecorded(products)

    def product(self, key, variants):
        return {"key": key, "name": key, "fulfilment_mode": "printful",
                "variants": [{"attributes": a, "aliases": list(s)} for a, s in variants]}

    def test_one_real_value_is_what_the_placeholder_meant(self):
        """Two channels described the same thing; only one named it."""
        resolved = self.resolve([self.product("towel", [
            ({"Colour": "White", "Size": '30"'}, ["etsy:1"]),
            ({"Colour": "Not specified", "Size": "Not specified"}, ["medusa:1"]),
        ])])
        self.assertEqual(len(resolved[0]["variants"]), 1)
        variant = resolved[0]["variants"][0]
        self.assertEqual(variant["attributes"], {"Colour": "White", "Size": '30"'})
        self.assertEqual(sorted(variant["aliases"]), ["etsy:1", "medusa:1"])

    def test_a_reviewed_answer_resolves_what_evidence_cannot(self):
        resolved = self.resolve([self.product("cap-denim", [
            ({"Secondary colour": "Blue"}, ["a"]),
            ({"Secondary colour": "White"}, ["b"]),
            ({"Secondary colour": "Not specified"}, ["c"]),
        ])])
        colours = {v["attributes"]["Secondary colour"] for v in resolved[0]["variants"]}
        self.assertEqual(colours, {"Blue", "White"})
        white = next(v for v in resolved[0]["variants"]
                     if v["attributes"]["Secondary colour"] == "White")
        self.assertEqual(sorted(white["aliases"]), ["b", "c"])

    def test_an_unanswerable_placeholder_stops_the_run(self):
        """Guessing between several real values would invent history."""
        with self.assertRaisesRegex(Exception, "unrecorded"):
            self.resolve([self.product("mug", [
                ({"Colour": "Red"}, ["a"]),
                ({"Colour": "Blue"}, ["b"]),
                ({"Colour": "Not specified"}, ["c"]),
            ])])

    def test_the_reviewed_catalog_resolves_completely(self):
        from odoo.addons.usl_b2c_restore import private_evidence

        if not private_evidence.available("catalog-specification-2026-09-06.json"):
            self.skipTest("The pinned catalog specification is not mounted here.")
        products = private_evidence.catalog_specification()
        resolved = self.resolve(products)
        for product in resolved:
            for variant in product["variants"]:
                self.assertNotIn(
                    "Not specified", variant["attributes"].values(),
                    f"{product['key']} still carries an unallocated attribute",
                )
        before = sum(len(v["aliases"]) for p in products for v in p["variants"])
        after = sum(len(v["aliases"]) for p in resolved for v in p["variants"])
        self.assertEqual(before, after, "resolution dropped a source identity")
