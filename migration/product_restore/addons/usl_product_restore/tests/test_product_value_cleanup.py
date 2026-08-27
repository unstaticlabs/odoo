from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "usl_product_restore_cleanup")
class TestProductValueCleanup(TransactionCase):
    def test_cleanup_removes_only_untraced_odoo_bot_zero_rows(self):
        product = self.env["product.product"].create(
            {
                "name": "Migration cleanup fixture",
                "company_id": self.env.company.id,
            },
        )
        generated = self.env["product.value"].create(
            {
                "product_id": product.id,
                "company_id": self.env.company.id,
                "user_id": self.env.ref("base.user_root").id,
                "description": "Price update from None to 0.0 by OdooBot",
                "value": 0,
                "date": "0001-01-01 00:00:00",
            },
        )
        source_zero = self.env["product.value"].create(
            {
                "product_id": product.id,
                "company_id": self.env.company.id,
                "user_id": self.env.user.id,
                "description": "Price update from None to 0.0 by Valentin",
                "value": 0,
                "date": "0001-01-01 00:00:00",
                "rebuild_source_model": "product.value",
                "rebuild_source_id": 999999,
            },
        )
        run = self.env["usl.product.restore.run"].new()

        removed = run._cleanup_generated_zero_product_values()

        self.assertGreaterEqual(removed, 1)
        self.assertFalse(generated.exists())
        self.assertTrue(source_zero.exists())

    def test_product_value_upsert_adopts_exact_row_and_removes_rerun_copy(self):
        product = self.env["product.product"].create(
            {
                "name": "Migration rerun fixture",
                "company_id": self.env.company.id,
            },
        )
        values = {
            "product_id": product.id,
            "company_id": self.env.company.id,
            "user_id": self.env.user.id,
            "description": "Price update from 0.0 to 4.0846 by Valentin",
            # product.value is monetary (EUR cents); the product's separate
            # standard_price retains the four-decimal 4.0846 source cost.
            "value": 4.08,
            "date": "2026-08-17 01:53:51",
        }
        first = self.env["product.value"].create(values)
        duplicate = self.env["product.value"].create(values)
        run = self.env["usl.product.restore.run"].create(
            {
                "source_database": "test_source",
                "source_snapshot": "source-test",
            },
        )
        row = {"id": 999998}

        adopted, removed = run._upsert_product_value(row, values)
        repeated, repeated_removed = run._upsert_product_value(row, values)

        self.assertEqual(adopted, first)
        self.assertEqual(repeated, first)
        self.assertEqual(removed, 1)
        self.assertEqual(repeated_removed, 0)
        self.assertFalse(duplicate.exists())
        self.assertEqual(adopted.rebuild_source_id, row["id"])
