"""Reading a drop of channel exports, end to end."""

from odoo.tests import TransactionCase, tagged
from odoo.tools.binary import BinaryBytes

from . import fixtures


@tagged("post_install", "-at_install", "usl_b2c_ingest")
class TestImportBatch(TransactionCase):
    """A batch reports what a drop contains without changing anything else."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env["res.company"].create({"name": "Ingest Test Company"})
        cls.env.user.company_ids = [(4, cls.company.id)]
        cls.env = cls.env(context=dict(cls.env.context, allowed_company_ids=[cls.company.id]))
        # A shop that invoices needs a chart: journals, receivables and payables
        # are what an invoice is made of, and a company without them proves
        # nothing about how one is posted.
        cls.env["account.chart.template"].try_loading(
            "generic_coa", company=cls.company, install_demo=False,
        )
        # The shop sells in euros, which is what its channels settle in.
        cls.company.currency_id = cls.env.ref("base.EUR")
        plan = cls.env["account.analytic.plan"].create({"name": "Channel"})
        cls.channels = {
            code: cls.env["b2c.channel"].create(
                {
                    "name": name,
                    "code": code,
                    "company_id": cls.company.id,
                    "analytic_account_id": cls.env["account.analytic.account"]
                    .create({"name": name, "plan_id": plan.id, "company_id": cls.company.id})
                    .id,
                },
            )
            for code, name in (("etsy", "Etsy"), ("medusa", "Medusa"))
        }

    def _batch(self, **files):
        batch = self.env["b2c.import.batch"].create(
            {"name": "Test drop", "company_id": self.company.id},
        )
        self.env["b2c.import.file"].create(
            [
                {"batch_id": batch.id, "name": name, "content": BinaryBytes(content)}
                for name, content in files.items()
            ],
        )
        return batch

    def _full_drop(self):
        return self._batch(
            **{
                "etsy-orders.csv": fixtures.etsy_orders(),
                "etsy-items.csv": fixtures.etsy_order_items(),
                "medusa-wide.csv": fixtures.medusa_orders(),
                "medusa-items.csv": fixtures.medusa_order_items(),
            },
        )

    def _order(self, channel, external_order_id, **values):
        return self.env["b2c.order"].create(
            {
                "name": f"B2C {external_order_id}",
                "canonical_key": values.pop("canonical_key", f"commerce:{external_order_id}"),
                "company_id": self.company.id,
                "channel_id": self.channels[channel].id,
                "source_provider": values.pop("source_provider", channel),
                "external_order_id": external_order_id,
                "order_date": "2026-03-04 00:00:00",
                "currency_id": self.company.currency_id.id,
            }
            | values,
        )

    def _issue_kinds(self, batch):
        return {issue.kind for issue in batch.issue_ids}

    def test_a_drop_reports_what_it_holds_without_changing_anything_else(self):
        before = self.env["b2c.order"].search_count([])
        batch = self._full_drop()
        batch.action_parse()
        self.assertEqual(batch.state, "parsed")
        self.assertEqual(self.env["b2c.order"].search_count([]), before)
        self.assertEqual(batch.order_count, 4)
        self.assertEqual(batch.new_order_count, 4)
        self.assertEqual(batch.known_order_count, 0)
        # Two Etsy lines, and four Medusa lines the two layouts agree on.
        self.assertEqual(batch.line_count, 6)
        self.assertEqual(str(batch.period_start), "2026-03-04")
        self.assertEqual(str(batch.period_end), "2026-03-09")

    def test_reading_the_same_drop_twice_finds_the_same_thing(self):
        batch = self._full_drop()
        batch.action_parse()
        first = (batch.row_count, batch.order_count, batch.new_order_count, len(batch.issue_ids))
        batch.action_parse()
        second = (batch.row_count, batch.order_count, batch.new_order_count, len(batch.issue_ids))
        self.assertEqual(first, second)

    def test_an_order_already_held_is_recognised_by_either_reference(self):
        self._order("etsy", "9000000001")
        self._order(
            "medusa",
            "order_internal_8000000001",
            external_display_id="8000000001",
        )
        batch = self._full_drop()
        batch.action_parse()
        self.assertEqual(batch.known_order_count, 2)
        self.assertEqual(batch.new_order_count, 2)
        self.assertFalse(batch.conflicting_count)

    def test_a_reference_held_twice_is_reported_rather_than_picked(self):
        self._order("medusa", "8000000001")
        self._order(
            "medusa",
            "order_internal_8000000001",
            external_display_id="8000000001",
            canonical_key="commerce:order_internal_8000000001",
        )
        batch = self._full_drop()
        batch.action_parse()
        self.assertIn("duplicate_order", self._issue_kinds(batch))
        duplicate = batch.issue_ids.filtered(lambda issue: issue.kind == "duplicate_order")
        self.assertEqual(len(duplicate), 1)
        self.assertEqual(duplicate.external_order_id, "8000000001")

    def test_an_order_held_on_another_channel_is_reported(self):
        self._order("etsy", "8000000001")
        batch = self._full_drop()
        batch.action_parse()
        self.assertIn("identity_conflict", self._issue_kinds(batch))

    def test_the_older_system_behind_a_channel_is_not_a_conflict(self):
        self._order("medusa", "8000000001", source_provider="medusa_legacy")
        batch = self._full_drop()
        batch.action_parse()
        self.assertNotIn("identity_conflict", self._issue_kinds(batch))
        self.assertEqual(batch.known_order_count, 1)

    def test_an_unreadable_file_is_named_and_the_rest_is_still_read(self):
        batch = self._batch(
            **{
                "etsy-orders.csv": fixtures.etsy_orders(),
                "something-else.csv": b"one,two,three\n1,2,3\n",
            },
        )
        batch.action_parse()
        self.assertIn("unreadable_file", self._issue_kinds(batch))
        self.assertEqual(batch.order_count, 2)
        unreadable = batch.file_ids.filtered(lambda item: item.state == "unreadable")
        self.assertIn("No parser recognises these columns", unreadable.note)

    def test_money_a_channel_does_not_export_is_reported_once_per_channel(self):
        batch = self._full_drop()
        batch.action_parse()
        missing = batch.issue_ids.filtered(lambda issue: issue.kind == "order_money_missing")
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing.severity, "advisory")
        self.assertIn("8000000001", missing.note)
        self.assertIn("8000000002", missing.note)

    def test_totals_that_stop_adding_up_are_reported(self):
        broken = [dict(fixtures.ETSY_ORDER_ROWS[0]) | {"Order Net": "40.00"}]
        batch = self._batch(**{"etsy-orders.csv": fixtures.etsy_orders(broken)})
        batch.action_parse()
        self.assertIn("net_identity", self._issue_kinds(batch))

    def test_a_reset_keeps_the_files_and_drops_what_was_found(self):
        batch = self._full_drop()
        batch.action_parse()
        batch.action_reset()
        self.assertEqual(batch.state, "draft")
        self.assertFalse(batch.row_ids)
        self.assertFalse(batch.issue_ids)
        self.assertEqual(len(batch.file_ids), 4)
