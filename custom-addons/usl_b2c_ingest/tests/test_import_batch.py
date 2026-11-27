"""Reading a drop of channel exports, end to end."""

from odoo.exceptions import AccessError, ValidationError
from odoo.tests import TransactionCase, new_test_user, tagged
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

    def test_original_csv_and_structured_rows_follow_the_reviewers_role(self):
        content = fixtures.medusa_order_items()
        batch = self._batch(**{"synthetic-items.csv": content})
        batch.action_parse()
        attachment = self.env["ir.attachment"].search([
            ("res_model", "=", "b2c.import.file"), ("res_id", "=", batch.file_ids.id),
            ("res_field", "=", "content"),
        ])
        self.assertEqual(len(attachment), 1)
        for role, original, payload in (
            ("reader", False, False),
            ("operator", True, False),
            ("sensitive_evidence", True, True),
        ):
            with self.subTest(role=role):
                user = new_test_user(
                    self.env, login=f"synthetic-csv-{role}",
                    groups=f"usl_b2c.group_b2c_{role}",
                    company_id=self.company.id, company_ids=[(6, 0, self.company.ids)],
                )
                source = batch.file_ids.with_user(user)
                row = batch.row_ids[:1].with_user(user)
                self.assertEqual(source.read(["name", "row_count"])[0]["name"], "synthetic-items.csv")
                self.assertTrue(row.read(["values"])[0]["values"])
                self.assertEqual("payload" in row.fields_get(["payload"]), payload)
                self.assertEqual("content" in source.fields_get(["content"]), original)
                self.assertEqual(attachment.with_user(user).has_access("read"), original)
                binary = source.env["ir.binary"]
                if original:
                    self.assertEqual(binary._get_stream_from(source, "content").read(), content)
                else:
                    with self.assertRaises(AccessError):
                        binary._get_stream_from(source, "content")

    def test_operator_can_upload_and_access_tracks_company_and_role_changes(self):
        other = self.env["res.company"].create({"name": "Second synthetic CSV shop"})
        operator = new_test_user(
            self.env, login="synthetic-csv-uploader", groups="usl_b2c.group_b2c_operator",
            company_id=self.company.id, company_ids=[(6, 0, (self.company | other).ids)],
        )
        batches = self.env["b2c.import.batch"].with_user(operator).with_context(
            allowed_company_ids=self.company.ids,
        )
        batch = batches.create({"name": "Synthetic upload", "company_id": self.company.id})
        content = fixtures.medusa_order_items()
        source = batches.env["b2c.import.file"].create({
            "batch_id": batch.id, "name": "synthetic-items.csv", "content": BinaryBytes(content),
        })
        self.assertEqual(source.env["ir.binary"]._get_stream_from(source, "content").read(), content)
        other_binary = source.with_context(allowed_company_ids=other.ids).env["ir.binary"]
        with self.assertRaises(AccessError):
            other_binary._find_record(res_model=source._name, res_id=source.id, field="content")

        operator.group_ids = [(3, self.env.ref("usl_b2c.group_b2c_operator").id),
                              (4, self.env.ref("usl_b2c.group_b2c_reader").id)]
        self.assertEqual(source.read(["name"])[0]["name"], "synthetic-items.csv")
        with self.assertRaises(AccessError):
            source.env["ir.binary"]._get_stream_from(source, "content")

    def test_import_details_follow_the_batch_company_selection(self):
        other = self.env["res.company"].create({"name": "Second synthetic shop"})
        companies = self.company | other
        reviewer = new_test_user(
            self.env, login="b2c-import-reviewer", groups="usl_b2c.group_b2c_reader",
            company_id=self.company.id, company_ids=[(6, 0, companies.ids)],
        )
        batches = self.env["b2c.import.batch"]
        for company in companies:
            batch = batches.with_company(company).create({"name": "Synthetic drop", "company_id": company.id})
            self.env["b2c.import.file"].create({
                "batch_id": batch.id, "name": "orders.csv", "content": BinaryBytes(fixtures.medusa_order_items()),
            })
            batch.with_company(company).action_parse()
            batches |= batch

        for model in ("b2c.import.file", "b2c.import.row", "b2c.import.issue"):
            details = self.env[model].sudo().search([("batch_id", "in", batches.ids)])
            self.assertEqual(set(details.batch_id.ids), set(batches.ids), model)
            for selection in (self.company, other, companies):
                with self.subTest(model=model, companies=selection.ids):
                    visible = self.env[model].with_user(reviewer).with_context(
                        allowed_company_ids=selection.ids,
                    ).search([("batch_id", "in", batches.ids)])
                    expected = details.filtered(lambda item: item.batch_id.company_id in selection)
                    self.assertEqual(set(visible.ids), set(expected.ids))

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

    def test_the_fuller_of_two_records_of_one_sale_replaces_the_other(self):
        thin = self._order("medusa", "8000000001")
        full = self._order(
            "medusa",
            "order_internal_8000000001",
            external_display_id="8000000001",
            canonical_key="commerce:order_internal_8000000001",
            total_amount=75.0,
        )
        self.env["b2c.order.line"].create(
            {
                "order_id": full.id,
                "line_key": "medusa:8000000001:one",
                "original_name": "Invented Chain",
                "quantity": 1,
            },
        )
        batch = self._full_drop()
        batch.action_parse()
        issue = batch.issue_ids.filtered(lambda item: item.kind == "duplicate_order")
        issue.action_supersede_duplicate()
        self.assertEqual(thin.state, "cancelled")
        self.assertEqual(thin.superseded_by_id, full)
        self.assertFalse(full.superseded_by_id)

    def test_superseding_a_duplicate_settles_the_finding_it_names(self):
        """The action exists to settle it; leaving it blocking is no answer.

        The finding is raised when the files are read and nothing else revisits
        it, so without judging identity again it stays blocking — and the rows
        stay conflicting — long after the duplicate is gone.
        """
        self._order("medusa", "8000000001")
        self._order(
            "medusa",
            "order_internal_8000000001",
            external_display_id="8000000001",
            canonical_key="commerce:order_internal_8000000001",
            total_amount=75.0,
        )
        batch = self._full_drop()
        batch.action_parse()
        issue = batch.issue_ids.filtered(lambda item: item.kind == "duplicate_order")
        self.assertTrue(issue)
        issue.action_supersede_duplicate()
        self.assertFalse(
            batch.issue_ids.filtered(lambda item: item.kind == "duplicate_order"),
        )
        self.assertNotIn(
            "conflicting",
            batch.row_ids.filtered(
                lambda row: row.external_order_id == "8000000001",
            ).mapped("resolution"),
        )

    def test_an_order_that_does_not_come_to_what_its_lines_say_is_reported(self):
        """Two exports have to agree about what the goods came to.

        Where they do not, one of them means something other than what it is
        read as — which is how a subtotal stated before tax came to be read as
        the price of the goods.
        """
        shrunk = tuple(
            {**row, "Total": str(round(float(row["Total"]) / 2, 2))}
            for row in fixtures.MEDUSA_FULL_ROWS
        )
        batch = self._batch(
            **{
                "medusa-full.csv": fixtures.medusa_full_orders(shrunk),
                "medusa-items.csv": fixtures.medusa_order_items(),
            },
        )
        batch.action_parse()
        finding = batch.issue_ids.filtered(lambda issue: issue.kind == "order_totals_disagree")
        self.assertTrue(finding)
        self.assertEqual(finding[0].severity, "advisory")
        self.assertIn("out.", finding[0].note)

    def test_an_order_that_adds_up_is_not_reported(self):
        batch = self._batch(
            **{
                "medusa-full.csv": fixtures.medusa_full_orders(),
                "medusa-items.csv": fixtures.medusa_order_items(),
            },
        )
        batch.action_parse()
        self.assertFalse(
            batch.issue_ids.filtered(
                lambda issue: issue.kind == "order_totals_disagree",
            ),
        )

    def test_a_replaced_record_stops_being_the_sale(self):
        thin = self._order("medusa", "8000000001")
        full = self._order(
            "medusa",
            "order_internal_8000000001",
            external_display_id="8000000001",
            canonical_key="commerce:order_internal_8000000001",
        )
        thin.write({"state": "cancelled", "superseded_by_id": full.id})
        batch = self._full_drop()
        batch.action_parse()
        self.assertNotIn(
            "duplicate_order", {issue.kind for issue in batch.issue_ids},
        )
        self.assertEqual(batch.known_order_count, 1)

    def test_a_record_cannot_replace_itself(self):
        order = self._order("medusa", "8000000001")
        with self.assertRaises(ValidationError):
            order.write({"state": "cancelled", "superseded_by_id": order.id})

    def test_a_replaced_record_is_cancelled(self):
        thin = self._order("medusa", "8000000001")
        full = self._order(
            "medusa",
            "order_internal_8000000001",
            external_display_id="8000000001",
            canonical_key="commerce:order_internal_8000000001",
        )
        with self.assertRaises(ValidationError):
            thin.superseded_by_id = full

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
