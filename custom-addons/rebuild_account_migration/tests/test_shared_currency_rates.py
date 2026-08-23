from unittest.mock import patch

from odoo import fields
from odoo.tests import TransactionCase, tagged

ECB_PAYLOAD = b"""<gesmes:Envelope
    xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01"
    xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
  <Cube>
    <Cube time="2026-08-13"><Cube currency="USD" rate="1.1600"/></Cube>
    <Cube time="2026-08-14"><Cube currency="USD" rate="1.1700"/></Cube>
  </Cube>
</gesmes:Envelope>"""


@tagged("post_install", "-at_install", "rebuild_account_migration_unit")
class TestSharedCurrencyRates(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.eur = cls.env.ref("base.EUR")
        cls.usd = cls.env.ref("base.USD")
        cls.usd.active = True
        cls.env["res.company"].search([]).write({
            "rebuild_currency_rate_share_same_base": False,
        })
        cls.company_a = cls.env["res.company"].create({
            "name": "Shared-rate company A",
            "currency_id": cls.eur.id,
            "rebuild_currency_rate_provider": "ecb",
            "rebuild_currency_rate_coverage_start": "2026-08-13",
        })
        cls.company_b = cls.env["res.company"].create({
            "name": "Shared-rate company B",
            "currency_id": cls.eur.id,
            "rebuild_currency_rate_provider": "ecb",
        })

    def test_one_update_synchronizes_same_currency_companies(self):
        result = self.company_a._rebuild_update_shared_ecb_currency_rates(
            payload=ECB_PAYLOAD,
            retrieved_at="2026-08-14 16:05:00",
            backfill=True,
        )

        self.assertEqual(
            set(result["company_ids"]),
            set((self.company_a | self.company_b).ids),
        )
        rates = self.env["res.currency.rate"].search([
            ("company_id", "in", (self.company_a | self.company_b).ids),
            ("currency_id", "=", self.usd.id),
            ("name", "in", ["2026-08-13", "2026-08-14"]),
        ])
        self.assertEqual(len(rates), 4)
        for rate_date, expected in (
            (fields.Date.to_date("2026-08-13"), 1.16),
            (fields.Date.to_date("2026-08-14"), 1.17),
        ):
            dated = rates.filtered(lambda rate: rate.name == rate_date)
            self.assertEqual(len(dated), 2)
            self.assertEqual(set(dated.mapped("rate")), {expected})

    def test_manual_rate_remains_company_specific(self):
        manual = self.env["res.currency.rate"].create({
            "company_id": self.company_b.id,
            "currency_id": self.usd.id,
            "name": "2026-08-14",
            "rate": 1.25,
        })

        result = self.company_a._rebuild_update_shared_ecb_currency_rates(
            payload=ECB_PAYLOAD,
            retrieved_at="2026-08-14 16:05:00",
            backfill=True,
        )

        self.assertEqual(manual.rate, 1.25)
        self.assertEqual(result["preserved_manual_count"], 1)
        company_a_rate = self.env["res.currency.rate"].search([
            ("company_id", "=", self.company_a.id),
            ("currency_id", "=", self.usd.id),
            ("name", "=", "2026-08-14"),
        ])
        self.assertAlmostEqual(company_a_rate.rate, 1.17)

    def test_different_base_currency_is_not_synchronized(self):
        gbp = self.env.ref("base.GBP")
        company_gbp = self.env["res.company"].create({
            "name": "GBP base company",
            "currency_id": gbp.id,
            "rebuild_currency_rate_provider": "ecb",
        })

        self.company_a._rebuild_update_shared_ecb_currency_rates(
            payload=ECB_PAYLOAD,
            retrieved_at="2026-08-14 16:05:00",
        )

        self.assertFalse(self.env["res.currency.rate"].search([
            ("company_id", "=", company_gbp.id),
            ("name", "=", "2026-08-14"),
        ]))

    def test_cron_fetches_once_and_updates_each_company_once(self):
        with patch.object(
            type(self.company_a),
            "_rebuild_fetch_ecb_xml",
            autospec=True,
            return_value=(
                ECB_PAYLOAD,
                fields.Datetime.to_datetime("2026-08-14 16:05:00"),
                "https://example.invalid/ecb.xml",
            ),
        ) as fetch:
            self.env["res.company"]._cron_rebuild_update_currency_rates()

        fetch.assert_called_once()
        for company in self.company_a | self.company_b:
            self.assertEqual(
                self.env["res.currency.rate"].search_count([
                    ("company_id", "=", company.id),
                    ("currency_id", "=", self.usd.id),
                    ("name", "=", "2026-08-14"),
                ]),
                1,
            )

    def test_existing_provider_rows_are_backfilled_without_copying_manual(self):
        source = self.env["res.currency.rate"].create({
            "company_id": self.company_a.id,
            "currency_id": self.usd.id,
            "name": "2026-08-13",
            "rate": 1.16,
            "rebuild_rate_provider": "ecb",
            "rebuild_rate_retrieved_at": "2026-08-13 16:05:00",
        })
        manual = self.env["res.currency.rate"].create({
            "company_id": self.company_a.id,
            "currency_id": self.usd.id,
            "name": "2026-08-12",
            "rate": 1.30,
        })

        (self.company_a | self.company_b)._rebuild_synchronize_existing_shared_ecb_rates()

        copied = self.env["res.currency.rate"].search([
            ("company_id", "=", self.company_b.id),
            ("currency_id", "=", self.usd.id),
            ("name", "=", source.name),
        ])
        self.assertAlmostEqual(copied.rate, source.rate)
        self.assertFalse(self.env["res.currency.rate"].search([
            ("company_id", "=", self.company_b.id),
            ("currency_id", "=", self.usd.id),
            ("name", "=", manual.name),
        ]))
