"""Native Medusa identities and the historical export boundary."""

from odoo.tests import tagged
from odoo.exceptions import UserError
from odoo.addons.usl_b2c_ingest import parsers
from . import fixtures
from .test_import_batch import TestImportBatch

HEADER = ("order_id", "order_number", "line_id", "date", "order_status", "payment_status", "fulfillment_status", "currency", "sku", "product", "variant", "qty", "unit_price", "total")


def identified(*, order_id="order_synthetic_ambre", number="9900000001", line_ids=("item_synthetic_a", "item_synthetic_b")):
    return fixtures._csv(HEADER, [dict(order_id=order_id, order_number=number, line_id=line_id,
        date="2026-03-04", order_status="pending", payment_status="captured", fulfillment_status="fulfilled",
        currency="EUR", sku="SYNTH-AMBRE", product="Atelier Ambre", variant="Clair", qty="1", unit_price="10", total="10") for line_id in line_ids])


@tagged("post_install", "-at_install", "usl_medusa_identity")
class TestMedusaIdentity(TestImportBatch):
    def test_native_line_identity_keeps_identical_items_and_state(self):
        fmt = parsers.detect(identified())
        rows, _document = parsers.parse(fmt, "synthetic.csv", identified())
        self.assertEqual(fmt.format_id, "medusa_identified_items")
        lines = [row for row in rows if row.grain == "line"]
        self.assertEqual([row.external_line_id for row in lines], ["item_synthetic_a", "item_synthetic_b"])
        self.assertTrue(all(row.values["internal_order_id"] == "order_synthetic_ambre" for row in rows))
        order = next(row for row in rows if row.grain == "order")
        self.assertEqual(order.external_order_id, "9900000001")
        self.assertEqual(order.values["original_provider_state"], "pending")
        self.assertEqual(order.values["source_payment_state"], "captured")
        self.assertEqual(order.values["source_fulfilment_state"], "fulfilled")
        self.assertNotIn("total_amount", order.values)
        self.assertNotIn("customer_email", order.values)

    def test_native_identified_batch_replay_and_existing_native_order(self):
        order = self._order("medusa", "order_synthetic_ambre", external_display_id="9900000001")
        batch = self._batch(**{"native.csv": identified(number="9900000002")})
        before = self.env["b2c.order"].search_count([])
        for _ in range(2):
            batch.action_parse()
            self.assertEqual(batch.known_order_count, 1)
            self.assertEqual(batch.line_count, 2)
            self.assertEqual(set(batch.row_ids.order_id.ids), {order.id})
            self.assertEqual(set(batch.row_ids.batch_id.ids), {batch.id})
            self.assertEqual(batch.company_id, self.company)
        self.assertEqual(self.env["b2c.order"].search_count([]), before)

    def test_native_and_historical_identity_collision_is_not_picked(self):
        self._order("medusa", "order_synthetic_ambre", external_display_id="9900000002")
        self._order("medusa", "9900000001")
        batch = self._batch(**{"native.csv": identified()})
        batch.action_parse()
        self.assertTrue(batch.conflicting_count)
        self.assertEqual(batch.new_order_count, 0)

    def test_native_and_legacy_layouts_for_one_order_are_refused(self):
        import csv, io
        legacy_rows = list(csv.DictReader(io.StringIO(identified().decode("utf-8-sig"))))
        legacy = fixtures._csv(parsers.medusa.ITEMS_HEADER, legacy_rows)
        for files in ({"native.csv": identified(), "legacy.csv": legacy}, {"legacy.csv": legacy, "native.csv": identified()}):
            batch = self._batch(**files)
            with self.assertRaisesRegex(UserError, "9900000001.*historical and native"), self.cr.savepoint():
                batch.action_parse()
            self.assertFalse(batch.row_ids)

    def test_incomplete_or_repeated_native_identity_is_unreadable(self):
        for content in (identified(order_id=""), identified(line_ids=("",)), identified(line_ids=("item_synthetic_a", "item_synthetic_a"))):
            batch = self._batch(**{"native.csv": content})
            batch.action_parse()
            self.assertEqual(batch.file_ids.state, "unreadable")
            self.assertFalse(batch.row_ids)

    def test_native_commerce_keeps_ids_and_later_legacy_export_is_known(self):
        import csv, io
        batch = self._batch(**{"native.csv": identified()})
        batch.action_parse()
        header = batch.row_ids.filtered(lambda r: r.grain == "order")
        partner = self.env["res.partner"].create({"name": "Atelier Ambre", "country_id": self.env.ref("base.fr").id})
        batch.row_ids.filtered(lambda r: r.grain == "line").product_id = self.env["product.product"].create({"name": "Atelier Ambre", "type": "service"})
        order = batch._commerce_order(header, partner, {"sold": 20, "shipping": 0, "discount": 0, "fee": 0, "total": 20})
        self.assertEqual(order.external_order_id, "order_synthetic_ambre")
        self.assertEqual(order.external_display_id, "9900000001")
        self.assertEqual(set(order.line_ids.mapped("external_line_id")), {"item_synthetic_a", "item_synthetic_b"})
        legacy_rows = list(csv.DictReader(io.StringIO(identified().decode("utf-8-sig"))))
        legacy = fixtures._csv(parsers.medusa.ITEMS_HEADER, legacy_rows)
        for content in (identified(), legacy):
            later = self._batch(**{"later.csv": content})
            later.action_parse()
            self.assertEqual(later.known_order_count, 1)
            self.assertEqual(later._orders_to_create(), self.env["b2c.import.row"])
            self.assertEqual(later.row_ids.order_id, order)
        self.assertEqual(len(order.line_ids), 2)

    def test_partial_legacy_overlap_is_refused_without_replacing_prior_parse(self):
        import csv, io
        batch = self._batch(**{"native.csv": identified()})
        batch.action_parse()
        before = batch.row_ids.ids
        rows = list(csv.DictReader(io.StringIO(identified(line_ids=("item_synthetic_a",)).decode("utf-8-sig"))))
        from odoo.tools.binary import BinaryBytes
        self.env["b2c.import.file"].create({"batch_id": batch.id, "name": "partial.csv", "content": BinaryBytes(fixtures._csv(parsers.medusa.ITEMS_HEADER, rows))})
        with self.assertRaises(UserError), self.cr.savepoint():
            batch.action_parse()
        self.assertEqual(batch.row_ids.ids, before)

    def test_two_native_snapshots_with_conflicting_ids_are_blocking(self):
        batch = self._batch(**{"first.csv": identified(), "other.csv": identified(line_ids=("item_synthetic_c", "item_synthetic_d"))})
        with self.assertRaises(UserError), self.cr.savepoint():
            batch.action_parse()
        self.assertFalse(batch.row_ids)

    def test_same_native_line_price_change_is_a_conflict_not_a_second_line(self):
        changed = identified().replace(b",10,10", b",11,11")
        batch = self._batch(**{"first.csv": identified(), "changed.csv": changed})
        batch.action_parse()
        self.assertEqual(batch.line_count, 2)
        self.assertTrue(batch.issue_ids.filtered(lambda issue: issue.kind == "payload_conflict" and issue.severity == "blocking"))

    def test_selected_company_never_resolves_foreign_native_identity(self):
        other = self.env["res.company"].create({"name": "Atelier Corail"})
        self._order("medusa", "order_synthetic_ambre", external_display_id="9900000001")
        batch = self._batch(**{"native.csv": identified()})
        batch.company_id = other
        batch.with_context(allowed_company_ids=(self.company | other).ids).action_parse()
        self.assertEqual(batch.new_order_count, 1)
        self.assertFalse(batch.row_ids.order_id)

    def test_native_ids_survive_row_permutation(self):
        import csv, io
        rows = list(csv.DictReader(io.StringIO(identified().decode("utf-8-sig"))))
        for sequence in (rows, list(reversed(rows))):
            fmt = parsers.detect(fixtures._csv(HEADER, sequence))
            parsed, _ = parsers.parse(fmt, "synthetic.csv", fixtures._csv(HEADER, sequence))
            self.assertEqual({row.external_line_id for row in parsed if row.grain == "line"}, {"item_synthetic_a", "item_synthetic_b"})

    def test_parse_changed_payment_delivery_and_refund_states_does_not_update_commerce(self):
        from odoo.tools import SQL
        tables = ("account_move", "account_move_line", "account_partial_reconcile", "account_full_reconcile", "account_payment", "sale_order", "sale_order_line", "stock_move", "b2c_order", "b2c_order_line")
        def snapshot():
            self.env.flush_all()
            result = {}
            for table in tables:
                self.cr.execute(SQL("SELECT count(*), md5(coalesce(string_agg(to_jsonb(t)::text, '' ORDER BY id), '')) FROM %s t", SQL.identifier(table)))
                result[table] = self.cr.fetchone()
            return result
        self._order("medusa", "order_synthetic_ambre", external_display_id="9900000001")
        before = snapshot()
        for payment, delivery in (("captured", "not_fulfilled"), ("captured", "fulfilled"), ("refunded", "returned")):
            content = identified().replace(b",captured,fulfilled,", ("," + payment + "," + delivery + ",").encode())
            batch = self._batch(**{"snapshot.csv": content})
            batch.action_parse()
            self.assertEqual(batch.known_order_count, 1)
            header = batch.row_ids.filtered(lambda row: row.grain == "order")
            self.assertEqual(header.values["source_payment_state"], payment)
            self.assertEqual(header.values["source_fulfilment_state"], delivery)
            self.assertFalse(batch._orders_to_create())
        self.assertEqual(snapshot(), before)

    def test_native_response_projection_roundtrips_without_customer_fields(self):
        from odoo.addons.usl_b2c_ingest.parsers.medusa_exchange import export_items
        response = {"orders": [{"id": "order_synthetic_ambre", "display_id": 9900000001,
            "created_at": "2026-03-04T00:00:00Z", "status": "pending", "payment_status": "captured", "fulfillment_status": "fulfilled",
            "currency_code": "eur", "email": "private-canary@example.invalid", "shipping_address": {"address_1": "private-address-canary"},
            "items": [{"id": line, "variant_sku": "SYNTH-AMBRE", "product_title": "Atelier Ambre", "variant_title": "Clair", "quantity": 1, "unit_price": 10, "total": 0} for line in ("item_synthetic_a", "item_synthetic_b")]}]}
        content = export_items(response)
        self.assertNotIn(b"private-canary", content)
        self.assertNotIn(b"private-address-canary", content)
        batch = self._batch(**{"projected.csv": content})
        batch.action_parse()
        self.assertEqual(batch.line_count, 2)
        self.assertEqual(set(batch.row_ids.filtered(lambda r: r.grain == "line").mapped("external_line_id")), {"item_synthetic_a", "item_synthetic_b"})
        self.assertEqual({row.values["subtotal_amount"] for row in batch.row_ids if row.grain == "line"}, {"0"})
        response["orders"][0]["items"][0].pop("total")
        with self.assertRaises(ValueError):
            export_items(response)

    def test_stale_native_preview_reuses_order_imported_under_a_different_display(self):
        batch = self._batch(**{"native.csv": identified()})
        batch.action_parse()
        header = batch.row_ids.filtered(lambda row: row.grain == "order")
        self.assertEqual(header.resolution, "new")
        held = self._order("medusa", "order_synthetic_ambre", external_display_id="9900000002")
        before = self.env["b2c.order"].search_count([])
        self.assertFalse(batch._materialise_order(header))
        self.assertEqual(header.order_id, held)
        self.assertEqual(header.resolution, "known")
        self.assertEqual(self.env["b2c.order"].search_count([]), before)

    def test_one_native_order_with_two_displays_in_a_drop_is_not_materialised_twice(self):
        batch = self._batch(**{"first.csv": identified(), "changed.csv": identified(number="9900000002")})
        with self.assertRaises(UserError), self.cr.savepoint():
            batch.action_parse()
        self.assertFalse(batch.row_ids)

    def test_french_new_format_and_identity_recovery_are_native_translations(self):
        self.env["res.lang"]._activate_lang("fr_FR")
        self.env["ir.module.module"]._load_module_terms(["usl_b2c_ingest"], ["fr_FR"], overwrite=True)
        choices = dict(self.env["b2c.import.file"].with_context(lang="fr_FR")._selection_format())
        self.assertEqual(choices["medusa_identified_items"], "Medusa — articles avec identifiants natifs")
        invalid = self._batch(**{"invalid.csv": identified(order_id="")})
        invalid.with_context(lang="fr_FR").action_parse()
        self.assertEqual(invalid.file_ids.note, "Chaque article Medusa doit indiquer un order_id non vide et un line_id unique.")
        self._order("medusa", "order_synthetic_fr_held", external_display_id="9900000033")
        conflict = self._batch(**{"native-fr.csv": identified(number="9900000033")})
        conflict.with_context(lang="fr_FR").action_parse()
        issue = conflict.issue_ids.filtered(lambda row: row.kind == "identity_conflict")
        self.assertTrue(issue)
        self.assertIn("identités source", issue.name)
        self.assertIn("Fichier", issue.note)
        import csv, io
        historical = fixtures._csv(parsers.medusa.ITEMS_HEADER, list(csv.DictReader(io.StringIO(identified().decode("utf-8-sig")))))
        mixed = self._batch(**{"native.csv": identified(), "history.csv": historical})
        with self.assertRaisesRegex(UserError, "Gardez un seul instantané"), self.cr.savepoint():
            mixed.with_context(lang="fr_FR").action_parse()

        batch = self._batch(**{"first.csv": identified(), "changed.csv": identified(number="9900000002")})
        with self.assertRaisesRegex(UserError, "identifiants natifs"), self.cr.savepoint():
            batch.with_context(lang="fr_FR").action_parse()

    def test_offline_cli_produces_private_nonoverwriting_import_file(self):
        import json, subprocess, sys, tempfile
        from pathlib import Path
        from odoo.addons.usl_b2c_ingest.parsers import medusa_exchange
        response = {"order": {"id": "order_synthetic_ambre", "display_id": 9900000001,
            "created_at": "2026-03-04T00:00:00Z", "status": "pending", "payment_status": "captured", "fulfillment_status": "fulfilled",
            "currency_code": "eur", "items": [{"id": "item_synthetic_a", "title": "Atelier Ambre", "quantity": 1, "unit_price": 10, "total": 10}]}}
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "response.json"
            output = Path(directory) / "items.csv"
            source.write_text(json.dumps(response))
            command = [sys.executable, medusa_exchange.__file__, str(source), "--output", str(output)]
            subprocess.run(command, check=True, capture_output=True)
            self.assertEqual(output.stat().st_mode & 0o777, 0o600)
            before = output.read_bytes()
            self.assertNotEqual(subprocess.run(command, capture_output=True).returncode, 0)
            self.assertEqual(output.read_bytes(), before)
            batch = self._batch(**{"projected.csv": before})
            batch.action_parse()
            self.assertEqual(batch.line_count, 1)
            self.assertEqual(batch.file_ids.state, "recognised")

    def test_historical_order_keeps_its_reconstructed_lines_on_native_replay(self):
        order = self._order("medusa", "9900000001")
        line = self.env["b2c.order.line"].create({"order_id": order.id, "line_key": "synthetic-historical-line", "external_line_id": "synthetic-content-key", "original_name": "Atelier Ambre"})
        batch = self._batch(**{"native.csv": identified()})
        batch.action_parse()
        self.assertEqual(batch.known_order_count, 1)
        self.assertEqual(batch.row_ids.order_id, order)
        self.assertFalse(batch._orders_to_create())
        self.assertEqual(order.line_ids, line)
        self.assertEqual(line.external_line_id, "synthetic-content-key")


    def test_existing_native_order_with_same_display_and_different_native_id_is_conflicting(self):
        held = self._order("medusa", "order_synthetic_other", external_display_id="9900000001")
        batch = self._batch(**{"native.csv": identified()})
        batch.action_parse()
        self.assertEqual(batch.known_order_count, 0)
        self.assertTrue(batch.conflicting_count)
        self.assertFalse(batch._orders_to_create())
        self.assertEqual(batch.row_ids.order_id, held)
        self.assertEqual(held.external_order_id, "order_synthetic_other")
        issue = batch.issue_ids.filtered(lambda row: row.kind == "identity_conflict")
        self.assertIn("conflicting source identities", issue.name)
        self.assertIn("order_synthetic_other", issue.note)
        self.assertIn("order_synthetic_ambre", issue.note)


    def test_distinct_orders_with_different_layouts_coexist_in_one_batch(self):
        import csv, io
        rows = list(csv.DictReader(io.StringIO(identified(number="9900000002").decode("utf-8-sig"))))
        legacy = fixtures._csv(parsers.medusa.ITEMS_HEADER, rows)
        batch = self._batch(**{"native.csv": identified(), "historical.csv": legacy})
        batch.action_parse()
        self.assertEqual(batch.order_count, 2)
        self.assertEqual(batch.line_count, 4)
        self.assertEqual(set(batch.row_ids.mapped("external_order_id")), {"9900000001", "9900000002"})

    def test_two_snapshots_with_changed_source_states_are_not_silently_picked(self):
        changed = identified().replace(b",captured,fulfilled,", b",refunded,returned,")
        batch = self._batch(**{"first.csv": identified(), "later.csv": changed})
        batch.action_parse()
        issues = batch.issue_ids.filtered(lambda issue: issue.kind == "payload_conflict" and issue.severity == "blocking")
        self.assertTrue(issues)
        self.assertIn("source_payment_state", " ".join(issues.mapped("note")))
        self.assertIn("source_fulfilment_state", " ".join(issues.mapped("note")))
        self.assertIn("refunded", " ".join(issues.mapped("note")))
        self.assertIn("returned", " ".join(issues.mapped("note")))


from .test_materialise import TestMaterialise


@tagged("post_install", "-at_install", "usl_medusa_identity")
class TestMedusaIdentityMaterialise(TestMaterialise):
    def test_identified_apply_and_historical_replay_create_one_sale_with_two_lines(self):
        import csv, io
        product = self._product("Atelier Ambre")
        self._alias("medusa", product, original_sku="SYNTH-AMBRE", original_name="Atelier Ambre", original_variation="Clair")
        full = fixtures.medusa_full_orders([{
            "Order_ID": "order_synthetic_ambre", "Display_ID": "9900000001", "Date": "2026-03-04", "Order status": "pending",
            "Payment Status": "captured", "Fulfillment Status": "fulfilled", "Customer First name": "Atelier", "Customer Last name": "Ambre",
            "Shipping Country Code": "FR", "Currency Code": "EUR", "Subtotal": "20", "Shipping Total": "0", "Tax Total": "0", "Discount Total": "0", "Total": "20",
        }])
        before_moves = self.env["account.move"].search_count([])
        batch = self._batch(**{"orders.csv": full, "native.csv": identified()})
        batch.action_parse()
        batch.action_resolve()
        batch.action_apply()
        order = batch.row_ids.filtered(lambda row: row.grain == "order").order_id
        sale = order.sale_order_id
        self.assertEqual(order.external_order_id, "order_synthetic_ambre")
        self.assertEqual(order.external_display_id, "9900000001")
        self.assertEqual(set(order.line_ids.mapped("external_line_id")), {"item_synthetic_a", "item_synthetic_b"})
        self.assertEqual(len(sale.order_line.filtered(lambda line: line.product_id == product)), 2)
        batch.action_apply()
        legacy_rows = list(csv.DictReader(io.StringIO(identified().decode("utf-8-sig"))))
        later = self._batch(**{"orders.csv": full, "legacy.csv": fixtures._csv(parsers.medusa.ITEMS_HEADER, legacy_rows)})
        later.action_parse()
        later.action_resolve()
        later.action_apply()
        self.assertEqual(later.row_ids.order_id, order)
        self.assertEqual(order.sale_order_id, sale)
        self.assertEqual(len(order.line_ids), 2)
        self.assertEqual(self.env["b2c.order"].search_count([("company_id", "=", self.company.id), ("external_order_id", "=", "order_synthetic_ambre")]), 1)
        self.assertEqual(self.env["account.move"].search_count([]), before_moves)
