import io
import os
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import HttpCase, TransactionCase, tagged
from odoo.tools import file_open
from odoo.tools.pdf import OdooPdfFileReader, OdooPdfFileWriter

from odoo.addons.l10n_fr_pdp.models.account_edi_proxy_user import (
    AccountEdiProxyClientUser as NativePdpProxyUser,
)

LIVE_ENVIRONMENT = {"USL_EINVOICE_LIVE_ENABLED": "1"}


class EinvoiceTestDataMixin:
    @classmethod
    def _account(cls, code, name, account_type):
        account = cls.env["account.account"].search([
            ("code", "=", code),
            ("company_ids", "in", cls.company.id),
        ], limit=1)
        if account:
            return account
        vals = {
            "code": code,
            "name": name,
            "account_type": account_type,
            "company_ids": [Command.set([cls.company.id])],
        }
        if account_type in {"asset_receivable", "liability_payable"}:
            vals["reconcile"] = True
        return cls.env["account.account"].create(vals)

    @classmethod
    def _journal(cls, journal_type):
        journal = cls.env["account.journal"].search([
            ("company_id", "=", cls.company.id),
            ("type", "=", journal_type),
        ], limit=1)
        if journal:
            return journal
        return cls.env["account.journal"].create({
            "name": f"E-Invoice Test {journal_type.title()}",
            "code": f"EI{journal_type[:2].upper()}",
            "type": journal_type,
            "company_id": cls.company.id,
        })

    @classmethod
    def _tax(cls, amount, tax_use):
        tax = cls.env["account.tax"].search([
            ("company_id", "=", cls.company.id),
            ("type_tax_use", "=", tax_use),
            ("amount_type", "=", "percent"),
            ("amount", "=", amount),
        ], limit=1)
        if tax:
            return tax
        group = cls.env["account.tax.group"].search([
            ("company_id", "=", cls.company.id),
            ("name", "=", f"E-Invoice VAT {amount:g}%"),
        ], limit=1)
        if not group:
            group = cls.env["account.tax.group"].create({
                "name": f"E-Invoice VAT {amount:g}%",
                "company_id": cls.company.id,
                "country_id": cls.env.ref("base.fr").id,
            })
        return cls.env["account.tax"].create({
            "name": f"E-Invoice {tax_use.title()} VAT {amount:g}%",
            "amount": amount,
            "amount_type": "percent",
            "type_tax_use": tax_use,
            "company_id": cls.company.id,
            "country_id": cls.env.ref("base.fr").id,
            "tax_group_id": group.id,
            "ubl_cii_tax_category_code": "S",
        })

    @classmethod
    def _configure_company(cls):
        france = cls.env.ref("base.fr")
        euro = cls.env.ref("base.EUR")
        euro.active = True
        cls.company.write({
            "country_id": france.id,
            "account_fiscal_country_id": france.id,
            "currency_id": euro.id,
        })
        cls.purchase_journal = cls._journal("purchase")
        cls.sale_journal = cls._journal("sale")
        cls.bank_journal = cls._journal("bank")
        cls.income_account = cls._account(
            "EI706000",
            "E-Invoice Test Revenue",
            "income",
        )
        cls.expense_account = cls._account(
            "EI606000",
            "E-Invoice Test Expense",
            "expense",
        )
        cls.receivable_account = cls._account(
            "EI411000",
            "E-Invoice Test Receivable",
            "asset_receivable",
        )
        cls.payable_account = cls._account(
            "EI401000",
            "E-Invoice Test Payable",
            "liability_payable",
        )
        cls.bank_suspense_account = cls._account(
            "EI471000",
            "E-Invoice Test Bank Suspense",
            "asset_current",
        )
        cls.bank_suspense_account.reconcile = True
        cls.company.account_journal_suspense_account_id = (
            cls.bank_suspense_account
        )
        cls.bank_journal.suspense_account_id = cls.bank_suspense_account
        cls.company_bank = cls.env["res.partner.bank"].create({
            "account_number": "FR7630006000011234567890189",
            "partner_id": cls.company.partner_id.id,
            "company_id": cls.company.id,
            "allow_out_payment": True,
        })
        cls.tax_sale_20 = cls._tax(20.0, "sale")
        cls.tax_sale_10 = cls._tax(10.0, "sale")
        cls.tax_purchase_20 = cls._tax(20.0, "purchase")
        cls.tax_purchase_10 = cls._tax(10.0, "purchase")
        cls.company.write({
            "name": "USL E-Invoice Test Company",
            "country_id": france.id,
            "account_fiscal_country_id": france.id,
            "vat": "FR48983982950",
            "company_registry": "98398295000021",
            "peppol_eas": "0225",
            "peppol_endpoint": "983982950",
            "street": "1 rue de la Validation",
            "zip": "75001",
            "city": "Paris",
            "email": "company@example.invalid",
            "phone": "+33142000000",
            "peppol_purchase_journal_id": cls.purchase_journal.id,
            "account_peppol_proxy_state": "not_registered",
            "rebuild_einvoice_environment": "development",
            "rebuild_einvoice_provider": "odoo_pdp",
            "account_peppol_contact_email": "company@example.invalid",
            "account_peppol_phone_number": "+33142000000",
            "l10n_fr_pdp_send_to_ppf": False,
            "l10n_fr_pdp_pilot_phase": False,
        })
        cls.recipient = cls.env["res.partner"].create({
            "name": "French Electronic Invoice Recipient",
            "country_id": cls.env.ref("base.fr").id,
            "vat": "FR23334175221",
            "company_registry": "96851575905823",
            "peppol_eas": "0225",
            "peppol_endpoint": "968515759_96851575905823",
            "street": "16 rue de la Réception",
            "zip": "59000",
            "city": "Lille",
            "property_account_receivable_id": cls.receivable_account.id,
            "property_account_payable_id": cls.payable_account.id,
        })
        cls.manager = cls.env["res.users"].with_context(
            no_reset_password=True,
        ).create({
            "name": "E-Invoice Accounting Manager",
            "login": "einvoice.manager@example.invalid",
            "password": "einvoice.manager",
            "email": "einvoice.manager@example.invalid",
            "company_id": cls.company.id,
            "company_ids": [Command.set([cls.company.id])],
            "group_ids": [Command.set([
                cls.env.ref("base.group_user").id,
                cls.env.ref("account.group_account_manager").id,
            ])],
        })
        cls.reviewer = cls.env["res.users"].with_context(
            no_reset_password=True,
        ).create({
            "name": "E-Invoice Read-Only Accountant",
            "login": "einvoice.reviewer@example.invalid",
            "password": "einvoice.reviewer",
            "email": "einvoice.reviewer@example.invalid",
            "company_id": cls.company.id,
            "company_ids": [Command.set([cls.company.id])],
            "group_ids": [Command.set([
                cls.env.ref("base.group_user").id,
                cls.env.ref("account.group_account_readonly").id,
                cls.env.ref(
                    "rebuild_account_migration."
                    "group_rebuild_accountant_reviewer",
                ).id,
            ])],
        })

    @classmethod
    def _create_persistent_received_invoice(
        cls,
        message_reference,
        *,
        invoice_reference="USL-SAFE-TEST",
    ):
        with file_open(
            "rebuild_account_migration/static/src/einvoice/"
            "representative_ubl_invoice.xml",
            "rb",
        ) as fixture:
            raw = fixture.read()
        raw = raw.replace(
            b"<cbc:ID>USL-SAFE-TEST</cbc:ID>",
            f"<cbc:ID>{invoice_reference}</cbc:ID>".encode(),
            1,
        )
        attachment = cls.env["ir.attachment"].create({
            "name": f"{message_reference}.xml",
            "raw": raw,
            "mimetype": "application/xml",
        })
        proxy_user = cls.env[
            "account_edi_proxy_client.user"
        ].sudo().new({
            "company_id": cls.company.id,
            "proxy_type": "pdp",
            "edi_mode": "demo",
        })
        proxy_user._peppol_import_invoice(
            attachment,
            "done",
            message_reference,
            journal=cls.purchase_journal,
        )
        return cls.env["rebuild.einvoice.reception"].search([
            ("company_id", "=", cls.company.id),
            ("provider_message_uuid", "=", message_reference),
        ], limit=1)


@tagged(
    "post_install",
    "-at_install",
    "rebuild_account_migration_unit",
    "einvoice_reception",
)
class TestFrenchEinvoiceReception(
    EinvoiceTestDataMixin,
    TransactionCase,
):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls._configure_company()
        cls.proxy_user = cls.env["account_edi_proxy_client.user"].new({
            "company_id": cls.company.id,
            "proxy_type": "pdp",
            "edi_mode": "demo",
        })

    def _source_invoice(
        self,
        *,
        move_type="out_invoice",
        currency=None,
        reference="SOURCE-EINVOICE-001",
        reversed_entry=None,
    ):
        lines = [
            Command.create({
                "name": "Representative service at 20% VAT",
                "account_id": self.income_account.id,
                "quantity": 1.0,
                "price_unit": 100.0,
                "tax_ids": [Command.set(self.tax_sale_20.ids)],
            }),
            Command.create({
                "name": "Representative service at 10% VAT",
                "account_id": self.income_account.id,
                "quantity": 1.0,
                "price_unit": 50.0,
                "tax_ids": [Command.set(self.tax_sale_10.ids)],
            }),
        ]
        move = self.env["account.move"].create({
            "move_type": move_type,
            "partner_id": self.recipient.id,
            "journal_id": self.sale_journal.id,
            "partner_bank_id": self.company_bank.id,
            "invoice_date": "2026-09-01",
            "currency_id": (currency or self.company.currency_id).id,
            "ref": reference,
            "reversed_entry_id": reversed_entry.id if reversed_entry else False,
            "invoice_line_ids": lines,
        })
        move.action_post()
        return move

    def _import(
        self,
        raw,
        message_reference,
        *,
        filename="incoming.xml",
        mimetype="application/xml",
        provider_state="done",
    ):
        attachment = self.env["ir.attachment"].create({
            "name": filename,
            "raw": raw,
            "mimetype": mimetype,
        })
        result = self.proxy_user._peppol_import_invoice(
            attachment,
            provider_state,
            message_reference,
            journal=self.purchase_journal,
        )
        evidence = self.env["rebuild.einvoice.reception"].search([
            ("company_id", "=", self.company.id),
            ("provider_message_uuid", "=", message_reference),
        ], limit=1)
        return result, evidence, attachment

    def _create_production_proxy_user(self):
        private_key = self.env[
            "certificate.key"
        ].sudo()._generate_rsa_private_key(
            self.company,
            name=f"pdp_prod_test_{self.company.id}.key",
        )
        return self.env["account_edi_proxy_client.user"].sudo().create({
            "id_client": f"pdp-prod-test-{self.company.id}",
            "company_id": self.company.id,
            "proxy_type": "pdp",
            "edi_mode": "prod",
            "edi_identification": f"0225:{self.company.peppol_endpoint}",
            "private_key_id": private_key.id,
            "refresh_token": "offline-test-token",
        })

    def test_readiness_offline_test_and_normal_bill_lifecycle(self):
        self.assertEqual(
            self.company.rebuild_einvoice_readiness_status,
            "not_verified",
        )
        self.assertEqual(
            self.company.rebuild_einvoice_capability_status,
            "not_verified",
        )
        self.assertEqual(
            self.company.rebuild_einvoice_next_action,
            "Run the reception self-check",
        )
        self.assertIn(
            "representative electronic invoice",
            self.company.rebuild_einvoice_next_steps,
        )

        self.company.write({
            "account_peppol_contact_email": "accounting@example.invalid",
            "account_peppol_phone_number": "+33612345678",
            "rebuild_einvoice_provider": "odoo_pdp",
        })
        before_counts = {
            "moves": self.env["account.move"].search_count([]),
            "partners": self.env["res.partner"].search_count([]),
            "attachments": self.env["ir.attachment"].search_count([]),
            "receptions": self.env[
                "rebuild.einvoice.reception"
            ].search_count([]),
        }
        proxy_model = self.env.registry["account_edi_proxy_client.user"]
        with patch.object(
            proxy_model,
            "_peppol_import_invoice",
            side_effect=AssertionError(
                "The self-check must not enter the commit-capable provider import.",
            ),
        ) as provider_import:
            action = self.company.with_user(
                self.manager,
            ).action_rebuild_run_einvoice_acceptance_test()
        provider_import.assert_not_called()

        self.assertEqual(action["tag"], "display_notification")
        self.assertEqual(self.company.rebuild_einvoice_test_status, "passed")
        self.assertTrue(self.company.rebuild_einvoice_test_current)
        self.assertFalse(self.company.rebuild_einvoice_test_reception_id)
        self.assertEqual(
            self.company.rebuild_einvoice_test_summary,
            "Reception self-check passed; no test bill was retained.",
        )
        self.assertEqual(
            self.company.rebuild_einvoice_capability_status,
            "test_passed",
        )
        self.assertEqual(
            {
                "moves": self.env["account.move"].search_count([]),
                "partners": self.env["res.partner"].search_count([]),
                "attachments": self.env["ir.attachment"].search_count([]),
                "receptions": self.env[
                    "rebuild.einvoice.reception"
                ].search_count([]),
            },
            before_counts,
        )
        self.assertEqual(
            self.company.rebuild_einvoice_readiness_status,
            "ready_inactive",
        )
        self.assertEqual(
            self.company.rebuild_einvoice_next_action,
            "Prepare production activation",
        )
        self.assertFalse(self.company.rebuild_einvoice_exchange_enabled)

        source = self._source_invoice(reference="SELF-CHECK-LIFECYCLE")
        payload, errors = self.env[
            "account.edi.xml.ubl_21_fr"
        ]._export_invoice(source)
        self.assertFalse(errors)
        _result, evidence, _attachment = self._import(
            payload,
            "normal-bill-lifecycle",
        )
        bill = evidence.move_id
        bill.action_post()
        payment = self.env["account.payment.register"].with_context(
            active_model="account.move",
            active_ids=bill.ids,
        ).create({
            "payment_date": bill.date,
            "journal_id": self.bank_journal.id,
        })._create_payments()
        self.assertTrue(payment)
        self.assertEqual(bill.payment_state, "paid")
        self.assertTrue(
            bill.line_ids.filtered(
                lambda line: (
                    line.account_id.account_type == "liability_payable"
                ),
            ).reconciled,
        )

    def test_production_preparation_is_explicit_audited_and_side_effect_free(self):
        self.assertEqual(
            self.env["res.company"].default_get([
                "rebuild_einvoice_environment",
            ])["rebuild_einvoice_environment"],
            "development",
        )
        self.company.with_user(
            self.manager,
        ).action_rebuild_run_einvoice_acceptance_test()
        before = {
            "exchange_enabled": self.company.rebuild_einvoice_exchange_enabled,
            "activation_approved": self.company.rebuild_einvoice_activation_approved,
            "pdp_state": self.company.account_peppol_proxy_state,
            "pdp_send": self.company.l10n_fr_pdp_send_to_ppf,
        }
        proxy_model = self.env.registry["account_edi_proxy_client.user"]
        settings_model = self.env.registry["res.config.settings"]
        with patch.dict(os.environ, LIVE_ENVIRONMENT, clear=False), patch.object(
            proxy_model,
            "_call_peppol_proxy",
            side_effect=AssertionError("Preparation must not contact the platform."),
        ) as provider_call, patch.object(
            settings_model,
            "action_open_peppol_form",
            side_effect=AssertionError("Preparation must not open registration."),
        ) as registration_action:
            action = self.company.with_user(
                self.manager,
            ).action_rebuild_prepare_einvoice_activation()

        provider_call.assert_not_called()
        registration_action.assert_not_called()
        self.assertEqual(action["tag"], "display_notification")
        self.assertEqual(self.company.rebuild_einvoice_environment, "production")
        self.assertEqual(
            self.company.rebuild_einvoice_readiness_status,
            "activation_required",
        )
        self.assertEqual(
            self.company.rebuild_einvoice_production_prepared_by_id,
            self.manager,
        )
        self.assertTrue(self.company.rebuild_einvoice_production_prepared_at)
        self.assertEqual(
            {
                "exchange_enabled": self.company.rebuild_einvoice_exchange_enabled,
                "activation_approved": self.company.rebuild_einvoice_activation_approved,
                "pdp_state": self.company.account_peppol_proxy_state,
                "pdp_send": self.company.l10n_fr_pdp_send_to_ppf,
            },
            before,
        )

        prepared_at = self.company.rebuild_einvoice_production_prepared_at
        repeat = self.company.with_user(
            self.manager,
        ).action_rebuild_prepare_einvoice_activation()
        self.assertEqual(repeat["params"]["type"], "info")
        self.assertEqual(
            self.company.rebuild_einvoice_production_prepared_at,
            prepared_at,
        )

        self.company.with_user(
            self.manager,
        ).action_rebuild_revoke_einvoice_activation()
        self.assertEqual(self.company.rebuild_einvoice_environment, "development")
        self.assertFalse(self.company.rebuild_einvoice_production_prepared_by_id)
        self.assertFalse(self.company.rebuild_einvoice_production_prepared_at)
        self.assertFalse(self.company.rebuild_einvoice_activation_approved)
        self.assertFalse(self.company.rebuild_einvoice_exchange_enabled)

    def test_readiness_tracks_native_registration_and_reception_states(self):
        self.company.with_user(
            self.manager,
        ).action_rebuild_run_einvoice_acceptance_test()
        self.company.write({
            "rebuild_einvoice_environment": "production",
            "rebuild_einvoice_activation_approved": True,
        })
        self._create_production_proxy_user()
        self.company.pdp_kyc_status = "success"

        other_company = self.env["res.company"].create({
            "name": "Independent E-Invoice Company",
            "rebuild_einvoice_last_poll_status": "temporary_failure",
        })
        self.assertEqual(
            other_company.rebuild_einvoice_readiness_status,
            "needs_attention",
        )

        proxy_model = self.env.registry["account_edi_proxy_client.user"]
        with patch.dict(
            os.environ,
            LIVE_ENVIRONMENT,
            clear=False,
        ), patch.object(
            proxy_model,
            "_call_peppol_proxy",
            side_effect=AssertionError(
                "Computing readiness must not contact the provider.",
            ),
        ) as provider_call:
            self.assertEqual(
                self.company.rebuild_einvoice_readiness_status,
                "activation_required",
            )

            self.company.account_peppol_proxy_state = "smp_registration"
            self.assertEqual(
                self.company.rebuild_einvoice_readiness_status,
                "registration_in_progress",
            )
            self.assertEqual(
                self.company.rebuild_einvoice_connection_status,
                "registration_pending",
            )
            self.assertFalse(self.company.rebuild_einvoice_exchange_enabled)
            self.assertEqual(
                self.company.rebuild_einvoice_next_action,
                "Registration in progress",
            )

            self.company.account_peppol_proxy_state = "receiver"
            self.assertEqual(
                self.company.rebuild_einvoice_readiness_status,
                "activation_required",
            )
            self.assertEqual(
                self.company.rebuild_einvoice_connection_status,
                "connected_suspended",
            )

            self.company.rebuild_einvoice_exchange_enabled = True
            self.assertEqual(
                self.company.rebuild_einvoice_readiness_status,
                "active",
            )

            self.company.account_peppol_proxy_state = "rejected"
            self.assertEqual(
                self.company.rebuild_einvoice_readiness_status,
                "needs_attention",
            )

            for poll_status in ("authentication", "temporary_failure"):
                with self.subTest(poll_status=poll_status):
                    self.company.write({
                        "account_peppol_proxy_state": "receiver",
                        "rebuild_einvoice_last_poll_status": poll_status,
                    })
                    self.assertEqual(
                        self.company.rebuild_einvoice_readiness_status,
                        "needs_attention",
                    )

            self.company.rebuild_einvoice_last_poll_status = "passed"
            self.assertEqual(
                self.company.rebuild_einvoice_readiness_status,
                "active",
            )

        provider_call.assert_not_called()
        self.assertEqual(
            other_company.rebuild_einvoice_readiness_status,
            "needs_attention",
        )
        self.assertFalse(other_company.rebuild_einvoice_exchange_enabled)
        self.assertFalse(self.company.l10n_fr_pdp_send_to_ppf)
        self.assertFalse(self.company.l10n_fr_pdp_pilot_phase)

        readiness_labels = dict(
            self.company._fields[
                "rebuild_einvoice_readiness_status"
            ]._description_selection(self.env),
        )
        self.assertEqual(
            readiness_labels["registration_in_progress"],
            "Registration in progress",
        )

    def test_production_preparation_enforces_role_company_and_single_record(self):
        with self.assertRaises(AccessError):
            self.company.with_user(
                self.reviewer,
            ).action_rebuild_prepare_einvoice_activation()

        other_company = self.env["res.company"].create({
            "name": "Unauthorized E-Invoice Company",
        })
        with self.assertRaises(AccessError):
            other_company.with_user(
                self.manager,
            ).action_rebuild_prepare_einvoice_activation()
        with self.assertRaises(ValueError):
            (self.company | other_company).with_user(
                self.manager,
            ).action_rebuild_prepare_einvoice_activation()

    def test_production_preparation_rejects_incomplete_stale_and_unguarded_state(self):
        self.company.account_peppol_contact_email = False
        with self.assertRaisesRegex(UserError, "Production activation cannot"):
            self.company.with_user(
                self.manager,
            ).action_rebuild_prepare_einvoice_activation()

        self.company.account_peppol_contact_email = "company@example.invalid"
        self.company.with_user(
            self.manager,
        ).action_rebuild_run_einvoice_acceptance_test()
        self.company.account_peppol_contact_email = "changed@example.invalid"
        with patch.dict(os.environ, LIVE_ENVIRONMENT, clear=False):
            with self.assertRaisesRegex(UserError, "offline reception test"):
                self.company.with_user(
                    self.manager,
                ).action_rebuild_prepare_einvoice_activation()

        self.company.account_peppol_contact_email = "company@example.invalid"
        self.company.with_user(
            self.manager,
        ).action_rebuild_run_einvoice_acceptance_test()
        self.company.rebuild_einvoice_test_status = "failed"
        with patch.dict(os.environ, LIVE_ENVIRONMENT, clear=False):
            with self.assertRaisesRegex(UserError, "offline reception test"):
                self.company.with_user(
                    self.manager,
                ).action_rebuild_prepare_einvoice_activation()

        self.company.with_user(
            self.manager,
        ).action_rebuild_run_einvoice_acceptance_test()
        self.env["ir.config_parameter"].sudo().set_str(
            "account_peppol.edi.mode",
            "demo",
        )
        with patch.dict(os.environ, LIVE_ENVIRONMENT, clear=False):
            with self.assertRaisesRegex(UserError, "safe demo onboarding"):
                self.company.with_user(
                    self.manager,
                ).action_rebuild_prepare_einvoice_activation()

        self.env["ir.config_parameter"].sudo().set_str(
            "account_peppol.edi.mode",
            "prod",
        )
        with patch.dict(
            os.environ,
            {"USL_EINVOICE_LIVE_ENABLED": "0"},
            clear=False,
        ):
            with self.assertRaisesRegex(UserError, "has not authorized"):
                self.company.with_user(
                    self.manager,
                ).action_rebuild_prepare_einvoice_activation()

    def test_production_preparation_button_is_manager_only(self):
        view_id = self.env.ref(
            "rebuild_account_migration."
            "view_company_rebuild_einvoice_readiness_form",
        ).id
        manager_arch = self.env["res.company"].with_user(self.manager).get_view(
            view_id=view_id,
            view_type="form",
        )["arch"]
        reviewer_arch = self.env["res.company"].with_user(self.reviewer).get_view(
            view_id=view_id,
            view_type="form",
        )["arch"]
        self.assertIn("action_rebuild_prepare_einvoice_activation", manager_arch)
        self.assertNotIn("action_rebuild_prepare_einvoice_activation", reviewer_arch)
        self.assertIn("registration_in_progress", manager_arch)
        self.assertIn("Registration in progress", manager_arch)

    def test_self_check_is_invalidated_by_material_configuration_change(self):
        self.company.write({
            "account_peppol_contact_email": "accounting@example.invalid",
            "account_peppol_phone_number": "+33612345678",
        })
        self.company.with_user(
            self.manager,
        ).action_rebuild_run_einvoice_acceptance_test()
        fingerprint = self.company.rebuild_einvoice_test_fingerprint
        self.assertTrue(self.company.rebuild_einvoice_test_current)

        self.company.account_peppol_contact_email = "new-contact@example.invalid"

        self.assertEqual(
            self.company.rebuild_einvoice_test_fingerprint,
            fingerprint,
        )
        self.assertFalse(self.company.rebuild_einvoice_test_current)
        self.assertEqual(
            self.company.rebuild_einvoice_readiness_status,
            "not_verified",
        )

    def test_failed_self_check_is_nonpolluting_and_actionable(self):
        before_counts = {
            "moves": self.env["account.move"].search_count([]),
            "partners": self.env["res.partner"].search_count([]),
            "attachments": self.env["ir.attachment"].search_count([]),
            "receptions": self.env[
                "rebuild.einvoice.reception"
            ].search_count([]),
        }
        move_model = self.env.registry["account.move"]
        with patch.object(
            move_model,
            "_get_edi_decoder",
            side_effect=UserError("Synthetic decoder failure"),
        ):
            action = self.company.with_user(
                self.manager,
            ).action_rebuild_run_einvoice_acceptance_test()

        self.assertEqual(action["params"]["type"], "warning")
        self.assertEqual(self.company.rebuild_einvoice_test_status, "failed")
        self.assertFalse(self.company.rebuild_einvoice_test_current)
        self.assertIn(
            "could not be validated",
            self.company.rebuild_einvoice_test_summary,
        )
        self.assertEqual(
            {
                "moves": self.env["account.move"].search_count([]),
                "partners": self.env["res.partner"].search_count([]),
                "attachments": self.env["ir.attachment"].search_count([]),
                "receptions": self.env[
                    "rebuild.einvoice.reception"
                ].search_count([]),
            },
            before_counts,
        )

    def test_upgrade_cleanup_removes_orphaned_legacy_self_check_bill(self):
        evidence = self._create_persistent_received_invoice(
            "offline-test-legacy-commit",
            invoice_reference="USL-SAFE-TEST-LEGACY-COMMIT",
        )
        bill = evidence.move_id
        evidence.attachment_id.name = "USL-SAFE-TEST-LEGACY-COMMIT.xml"
        evidence.move_id = False

        self.company._rebuild_cleanup_legacy_einvoice_self_checks()

        self.assertFalse(bill.exists())
        self.assertFalse(evidence.exists())
        self.assertEqual(self.company.rebuild_einvoice_test_status, "not_run")
        self.assertIn(
            "non-polluting self-check",
            self.company.rebuild_einvoice_test_summary,
        )

    def test_safe_defaults_and_native_demo_provider_are_network_free(self):
        self.env["ir.config_parameter"].sudo().set_str(
            "account_peppol.edi.mode",
            "demo",
        )
        self.company.write({
            "rebuild_einvoice_provider": False,
            "peppol_purchase_journal_id": False,
            "peppol_eas": False,
            "peppol_endpoint": False,
            "account_peppol_contact_email": False,
            "account_peppol_phone_number": False,
            "l10n_fr_pdp_send_to_ppf": True,
        })
        self.env["res.company"]._rebuild_apply_default_einvoice_provider()

        self.assertEqual(self.company.rebuild_einvoice_provider, "odoo_pdp")
        self.assertEqual(
            self.company.peppol_purchase_journal_id,
            self.purchase_journal,
        )
        self.assertEqual(self.company.peppol_eas, "0225")
        self.assertEqual(self.company.peppol_endpoint, "983982950")
        self.assertEqual(
            self.company.account_peppol_contact_email,
            "company@example.invalid",
        )
        self.assertEqual(
            self.company.account_peppol_phone_number,
            "+33142000000",
        )
        self.assertFalse(self.company.l10n_fr_pdp_send_to_ppf)
        self.assertFalse(self.company.l10n_fr_pdp_pilot_phase)
        self.assertFalse(self.company.rebuild_einvoice_exchange_enabled)
        self.assertEqual(
            self.env["ir.config_parameter"].sudo().get_str(
                "account_peppol.edi.mode",
            ),
            "demo",
        )
        self.company.write({
            "peppol_eas": "0002",
            "peppol_endpoint": "CUSTOM-IDENTIFIER",
        })
        self.env["res.company"]._rebuild_apply_default_einvoice_provider()
        self.assertEqual(self.company.peppol_eas, "0002")
        self.assertEqual(self.company.peppol_endpoint, "CUSTOM-IDENTIFIER")

        wizard = self.env["pdp.registration"].with_user(
            self.env.ref("base.user_admin"),
        ).with_company(self.company).with_context(
            rebuild_einvoice_safe_demo=True,
        ).create({
            "company_id": self.company.id,
        })
        with (
            patch(
                "odoo.addons.l10n_fr_pdp.wizard.pdp_registration."
                "iap_tools.iap_jsonrpc",
            ) as identity_call,
            patch(
                "odoo.addons.account_edi_proxy_client.models."
                "account_edi_proxy_user.requests.post",
            ) as proxy_call,
            patch(
                "odoo.addons.l10n_fr_pdp.models.res_partner.requests.get",
            ) as directory_call,
        ):
            wizard.button_trigger_authentication()
            self.assertEqual(self.company.pdp_kyc_status, "success")
            wizard.button_register_pdp_participant()
            self.assertFalse(self.company.rebuild_einvoice_exchange_enabled)
            demo_user = self.company.account_edi_proxy_client_ids.filtered(
                lambda user: (
                    user.proxy_type == "pdp" and user.edi_mode == "demo"
                ),
            )
            self.assertTrue(demo_user)
            demo_user._peppol_get_new_documents()
            demo_evidence = self.env["rebuild.einvoice.reception"].search([
                ("company_id", "=", self.company.id),
                (
                    "provider_message_uuid",
                    "=",
                    f"{self.company.id}_demo_vendor_bill",
                ),
            ])
            self.assertEqual(demo_evidence.status, "bill_created")
            self.assertTrue(demo_evidence.is_test)
            self.assertEqual(demo_evidence.move_id.state, "draft")
            evidence_count = self.env[
                "rebuild.einvoice.reception"
            ].search_count([
                ("company_id", "=", self.company.id),
                (
                    "provider_message_uuid",
                    "=",
                    f"{self.company.id}_demo_vendor_bill",
                ),
            ])
            demo_user._peppol_get_new_documents()
            self.assertEqual(
                self.env["rebuild.einvoice.reception"].search_count([
                    ("company_id", "=", self.company.id),
                    (
                        "provider_message_uuid",
                        "=",
                        f"{self.company.id}_demo_vendor_bill",
                    ),
                ]),
                evidence_count,
            )
            identity_call.assert_not_called()
            proxy_call.assert_not_called()
            directory_call.assert_not_called()

        self.assertEqual(
            self.company.rebuild_einvoice_connection_status,
            "test",
        )
        self.assertEqual(
            self.company.rebuild_einvoice_provider_contract_status,
            "not_verified",
        )
        self.company.with_user(
            self.manager,
        ).action_rebuild_run_einvoice_acceptance_test()
        self.env["ir.config_parameter"].sudo().set_str(
            "account_peppol.edi.mode",
            "prod",
        )
        self.company.rebuild_einvoice_environment = "production"
        with patch.dict(os.environ, LIVE_ENVIRONMENT, clear=False):
            self.company.with_user(
                self.manager,
            ).action_rebuild_approve_einvoice_activation()
        self.assertFalse(demo_user.active)
        self.assertEqual(
            self.env["ir.config_parameter"].sudo().get_str(
                "account_peppol.edi.mode",
            ),
            "prod",
        )
        self.assertTrue(self.company.rebuild_einvoice_activation_approved)
        self.assertEqual(
            self.company.rebuild_einvoice_provider_contract_status,
            "not_verified",
        )

    def test_cii_billing_period_without_complete_deferred_fields(self):
        cii = self.env["account.edi.cii"]
        start = fields.Date.to_date("2026-09-01")
        end = fields.Date.to_date("2026-09-30")
        for line_fields in ({}, {"deferred_start_date": object()}, {"deferred_end_date": object()}):
            for invoice_date, due_date in ((start, end), (False, end), (start, False), (False, False)):
                with self.subTest(fields=list(line_fields), start=invoice_date, end=due_date):
                    # A non-iterable fixture proves the fallback never reads
                    # absent line-level deferral data, including partial schemas.
                    invoice = SimpleNamespace(
                        invoice_line_ids=SimpleNamespace(_fields=line_fields),
                        invoice_date=invoice_date,
                        invoice_date_due=due_date,
                    )
                    node = cii._cii_get_billing_specified_period_node({"invoice": invoice})
                    self.assertEqual(node, {
                        "ram:StartDateTime": {
                            "udt:DateTimeString": {"_text": "20260901", "format": "102"},
                        } if invoice_date else None,
                        "ram:EndDateTime": {
                            "udt:DateTimeString": {"_text": "20260930", "format": "102"},
                        } if due_date else None,
                    })

    def test_cii_billing_period_preserves_native_complete_deferral_range(self):
        class DeferredLines(list):
            _fields = {"deferred_start_date": object(), "deferred_end_date": object()}

        invoice = SimpleNamespace(
            invoice_date=fields.Date.to_date("2026-09-01"),
            invoice_date_due=fields.Date.to_date("2026-09-30"),
            invoice_line_ids=DeferredLines([
                SimpleNamespace(
                    deferred_start_date=fields.Date.to_date("2026-08-01"),
                    deferred_end_date=fields.Date.to_date("2026-10-31"),
                ),
                SimpleNamespace(deferred_start_date=False, deferred_end_date=False),
            ]),
        )
        node = self.env["account.edi.cii"]._cii_get_billing_specified_period_node({"invoice": invoice})
        self.assertEqual(node, {
            "ram:StartDateTime": {
                "udt:DateTimeString": {"_text": "20260801", "format": "102"},
            },
            "ram:EndDateTime": {
                "udt:DateTimeString": {"_text": "20261031", "format": "102"},
            },
        })

    def test_supported_formats_credit_notes_taxes_and_currencies(self):
        source = self._source_invoice()
        ubl, ubl_errors = self.env[
            "account.edi.xml.ubl_21_fr"
        ]._export_invoice(source)
        self.assertFalse(ubl_errors)
        ubl_result, ubl_evidence, _attachment = self._import(
            ubl,
            "format-ubl-invoice",
        )
        self.assertEqual(ubl_evidence.status, "bill_created")
        self.assertEqual(ubl_evidence.document_format, "ubl")
        self.assertEqual(ubl_result["move"].move_type, "in_invoice")
        self.assertEqual(len(ubl_result["move"].invoice_line_ids), 2)
        self.assertEqual(
            set(
                ubl_result["move"].invoice_line_ids.tax_ids.mapped("amount"),
            ),
            {10.0, 20.0},
        )

        credit = self._source_invoice(
            move_type="out_refund",
            reference="SOURCE-CREDIT-001",
            reversed_entry=source,
        )
        credit_ubl, credit_errors = self.env[
            "account.edi.xml.ubl_21_fr"
        ]._export_invoice(credit)
        self.assertFalse(credit_errors)
        credit_result, credit_evidence, _attachment = self._import(
            credit_ubl,
            "format-ubl-credit",
            filename="incoming-credit.xml",
        )
        self.assertEqual(credit_evidence.document_kind, "credit_note")
        self.assertEqual(credit_result["move"].move_type, "in_refund")
        self.assertEqual(credit_result["move"].state, "draft")

        usd = self.env.ref("base.USD")
        usd.active = True
        self.env["res.currency.rate"].create({
            "name": "2026-09-01",
            "currency_id": usd.id,
            "company_id": self.company.id,
            "rate": 1.2,
        })
        currency_source = self._source_invoice(
            currency=usd,
            reference="SOURCE-USD-001",
        )
        cii, cii_errors = self.env[
            "account.edi.xml.cii"
        ]._export_invoice(currency_source)
        self.assertFalse(cii_errors)
        cii_result, cii_evidence, _attachment = self._import(
            cii,
            "format-cii-usd",
            filename="incoming-cii.xml",
        )
        self.assertEqual(cii_evidence.document_format, "cii")
        self.assertEqual(cii_result["move"].currency_id, usd)
        self.assertEqual(cii_result["move"].move_type, "in_invoice")

        with file_open(
            "account_edi_ubl_cii/tests/test_files/import/bis3/invoice/"
            "be/test_import_invoice_auto_generate_pdf.pdf",
            "rb",
        ) as source_pdf:
            reader_buffer = io.BytesIO(source_pdf.read())
        writer = OdooPdfFileWriter()
        writer.cloneReaderDocumentRoot(
            OdooPdfFileReader(reader_buffer, strict=False),
        )
        writer.addAttachment(
            "factur-x.xml",
            cii,
            subtype="text/xml",
            afrelationship="/Alternative",
        )
        facturx_buffer = io.BytesIO()
        writer.write(facturx_buffer)
        facturx_result, facturx_evidence, _attachment = self._import(
            facturx_buffer.getvalue(),
            "format-facturx-usd",
            filename="incoming-factur-x.pdf",
            mimetype="application/pdf",
        )
        self.assertEqual(facturx_evidence.document_format, "facturx")
        self.assertEqual(facturx_evidence.document_kind, "invoice")
        self.assertEqual(facturx_evidence.status, "bill_created")
        self.assertEqual(facturx_result["move"].currency_id, usd)

    def test_duplicate_idempotent_poll_malformed_rejection_and_retry(self):
        source = self._source_invoice(reference="SOURCE-DEDUPE-001")
        payload, errors = self.env[
            "account.edi.xml.ubl_21_fr"
        ]._export_invoice(source)
        self.assertFalse(errors)
        result, original, _attachment = self._import(
            payload,
            "dedupe-original",
        )
        original_bill = result["move"]

        repeated_result, repeated, repeated_attachment = self._import(
            payload,
            "dedupe-original",
            filename="same-message-again.xml",
        )
        self.assertEqual(repeated, original)
        self.assertEqual(repeated_result["move"], original_bill)
        self.assertEqual(
            self.env["rebuild.einvoice.reception"].search_count([
                ("company_id", "=", self.company.id),
                ("provider_message_uuid", "=", "dedupe-original"),
            ]),
            1,
        )
        self.assertEqual(
            repeated_attachment.res_model,
            "rebuild.einvoice.reception",
        )

        duplicate_result, duplicate, _attachment = self._import(
            payload,
            "dedupe-new-message",
            filename="same-document-new-message.xml",
        )
        self.assertNotIn("move", duplicate_result)
        self.assertEqual(duplicate.status, "duplicate")
        self.assertEqual(duplicate.duplicate_of_id, original)
        self.assertEqual(duplicate.move_id, original_bill)

        malformed_result, malformed, malformed_attachment = self._import(
            b"<Invoice><broken>",
            "malformed-document",
            filename="malformed.xml",
        )
        self.assertNotIn("move", malformed_result)
        self.assertEqual(malformed.status, "technical_error")
        self.assertEqual(malformed.failure_code, "invalid_document")
        self.assertEqual(malformed.attempt_count, 1)
        self.assertTrue(malformed.can_retry)
        self.assertEqual(
            malformed_attachment.res_model,
            "rebuild.einvoice.reception",
        )
        malformed.with_user(self.manager).action_retry_processing()
        self.assertEqual(malformed.status, "technical_error")
        self.assertEqual(malformed.attempt_count, 2)
        for expected_attempt in (3, 4, 5):
            malformed.with_user(self.manager).action_retry_processing()
            self.assertEqual(malformed.attempt_count, expected_attempt)
        self.assertFalse(malformed.can_retry)
        with self.assertRaisesRegex(
            UserError,
            "not available for another processing attempt",
        ):
            malformed.with_user(self.manager).action_retry_processing()

        rejected_payload = payload + b"\n"
        rejected_result, rejected, _attachment = self._import(
            rejected_payload,
            "platform-rejected",
            filename="platform-rejected.xml",
            provider_state="error",
        )
        self.assertTrue(rejected_result["move"])
        self.assertEqual(rejected.status, "rejected")
        self.assertFalse(rejected.can_retry)

    def test_native_approval_and_refusal_responses_are_fully_offline(self):
        self.env["ir.config_parameter"].sudo().set_str(
            "account_peppol.edi.mode",
            "demo",
        )
        registration = self.env["pdp.registration"].with_user(
            self.env.ref("base.user_admin"),
        ).with_company(self.company).with_context(
            rebuild_einvoice_safe_demo=True,
        ).create({
            "company_id": self.company.id,
        })
        registration.button_trigger_authentication()
        registration.button_register_pdp_participant()

        approval = self._create_persistent_received_invoice(
            "native-approval-response",
        ).move_id
        self.assertTrue(approval.pdp_can_send_response)
        with patch.object(
            NativePdpProxyUser,
            "_call_peppol_proxy",
            return_value={
                "messages": [{"message_uuid": "mocked-approval-response"}],
            },
        ) as provider_call:
            approval.action_post()
        provider_call.assert_called_once()
        approval_params = provider_call.call_args.kwargs["params"]
        self.assertEqual(approval_params["status"], "approved")
        self.assertTrue(approval_params["lifecycle"])
        self.assertEqual(
            approval_params["reference_uuids"],
            ["native-approval-response"],
        )
        self.assertEqual(
            approval.peppol_response_ids.filtered(
                lambda response: response.response_code == "AP",
            ).peppol_state,
            "processing",
        )

        refusal_evidence = self._create_persistent_received_invoice(
            "native-refusal-response",
            invoice_reference="USL-SAFE-REFUSAL",
        )
        refusal = refusal_evidence.move_id
        self.assertTrue(refusal.pdp_can_send_response)
        action = refusal.button_cancel()
        self.assertEqual(action["res_model"], "pdp.response.wizard")
        wizard = self.env[action["res_model"]].browse(action["res_id"])
        wizard.reason_code = False
        wizard.note = False
        with self.assertRaisesRegex(
            UserError,
            "select a Reason Code",
        ):
            wizard.button_send()
        wizard.write({
            "reason_code": "NON_CONFORME",
            "note": "Mandatory supplier information is missing.",
        })
        with patch.object(
            NativePdpProxyUser,
            "_call_peppol_proxy",
            return_value={
                "messages": [{"message_uuid": "mocked-refusal-response"}],
            },
        ) as provider_call:
            wizard.button_send()
        provider_call.assert_called_once()
        refusal_params = provider_call.call_args.kwargs["params"]
        self.assertEqual(refusal_params["status"], "refused")
        self.assertEqual(
            refusal_params["additional_info"]["native-refusal-response"],
            {
                "reason_code": "NON_CONFORME",
                "note": "Mandatory supplier information is missing.",
                "issue_datetime": refusal_params["additional_info"][
                    "native-refusal-response"
                ]["issue_datetime"],
            },
        )
        self.assertFalse(self.company.l10n_fr_pdp_send_to_ppf)

    def test_live_boundary_provider_recovery_and_reception_only_crons(self):
        self.company.write({
            "account_peppol_contact_email": "accounting@example.invalid",
            "account_peppol_phone_number": "+33612345678",
            "rebuild_einvoice_provider": "odoo_pdp",
        })
        self.company.with_user(
            self.manager,
        ).action_rebuild_run_einvoice_acceptance_test()
        live_proxy = self.env["account_edi_proxy_client.user"].new({
            "company_id": self.company.id,
            "proxy_type": "pdp",
            "edi_mode": "prod",
        })

        with patch.object(
            NativePdpProxyUser,
            "_call_peppol_proxy",
        ) as native_call:
            with self.assertRaisesRegex(
                UserError,
                "Production activation required",
            ):
                live_proxy._call_peppol_proxy(
                    "/api/pdp/1/get_all_documents",
                )
            native_call.assert_not_called()

        self.company.rebuild_einvoice_environment = "production"
        with patch.dict(os.environ, LIVE_ENVIRONMENT, clear=False):
            self.company.with_user(
                self.manager,
            ).action_rebuild_approve_einvoice_activation()
            self.assertTrue(self.company.rebuild_einvoice_activation_approved)
            self._create_production_proxy_user()
            self.company.pdp_kyc_status = "success"
            self.assertEqual(
                self.company.rebuild_einvoice_provider_contract_status,
                "verified",
            )

            with patch.object(
                NativePdpProxyUser,
                "_call_peppol_proxy",
                return_value={"messages": []},
            ) as native_call:
                result = live_proxy._call_peppol_proxy(
                    "/api/pdp/1/get_all_documents",
                )
                self.assertEqual(result, {"messages": []})
                native_call.assert_called_once()
            self.assertEqual(
                self.company.rebuild_einvoice_last_poll_status,
                "passed",
            )

            with patch.object(
                NativePdpProxyUser,
                "_call_peppol_proxy",
                side_effect=UserError("temporary connection timeout"),
            ):
                try:
                    live_proxy._call_peppol_proxy(
                        "/api/pdp/1/get_all_documents",
                    )
                except UserError:
                    pass
                else:
                    self.fail("The temporary provider failure was not raised.")
            self.assertEqual(
                self.company.rebuild_einvoice_last_poll_status,
                "temporary_failure",
            )

            with patch.object(
                NativePdpProxyUser,
                "_call_peppol_proxy",
                side_effect=UserError("invalid authentication token"),
            ):
                try:
                    live_proxy._call_peppol_proxy(
                        "/api/pdp/1/get_all_documents",
                    )
                except UserError:
                    pass
                else:
                    self.fail("The authentication failure was not raised.")
            self.assertEqual(
                self.company.rebuild_einvoice_last_poll_status,
                "authentication",
            )

            with patch.object(
                NativePdpProxyUser,
                "_pdp_get_regulatory_documents",
            ) as regulatory_call:
                live_proxy._pdp_get_regulatory_documents()
                regulatory_call.assert_called_once()
            with patch.object(
                NativePdpProxyUser,
                "_pdp_send_lifecycles",
            ) as lifecycle_call:
                self.assertIsNone(live_proxy._pdp_send_lifecycles())
                lifecycle_call.assert_not_called()
            with self.assertRaisesRegex(UserError, "E-reporting is inactive"):
                self.env["l10n.fr.pdp.reports.flow"].action_send()

            reception_crons = [
                self.env.ref(xmlid)
                for xmlid in (
                    "account_peppol.ir_cron_peppol_get_new_documents",
                    "account_peppol.ir_cron_peppol_get_message_status",
                    "account_peppol.ir_cron_peppol_get_participant_status",
                    "account_peppol.ir_cron_peppol_webhook_keepalive",
                    "l10n_fr_pdp.ir_cron_pdp_get_regulatory_documents",
                )
            ]
            restricted_crons = [
                self.env.ref(xmlid)
                for xmlid in (
                    "account_peppol_response.ir_cron_peppol_auto_register_services",
                    "l10n_fr_pdp.ir_cron_pdp_send_lifecycles",
                    "l10n_fr_pdp.ir_cron_l10n_fr_pdp_generate_flows",
                )
            ]
            restricted_cron_state = {
                cron.id: cron.active
                for cron in restricted_crons
            }
            self.company.account_peppol_proxy_state = "receiver"
            inactive_cron = reception_crons[0]
            inactive_cron.sudo().active = False
            with self.assertRaisesRegex(
                UserError,
                "Production reception scheduling is not ready",
            ):
                self.company.with_user(
                    self.manager,
                ).action_rebuild_enable_einvoice_exchange()
            self.assertFalse(self.company.rebuild_einvoice_exchange_enabled)
            inactive_cron.sudo().active = True
            self.company.with_user(
                self.manager,
            ).action_rebuild_enable_einvoice_exchange()
            self.assertTrue(all(cron.active for cron in reception_crons))
            self.assertEqual(
                {cron.id: cron.active for cron in restricted_crons},
                restricted_cron_state,
            )
            self.assertFalse(self.company.l10n_fr_pdp_send_to_ppf)
            self.assertTrue(self.company.rebuild_einvoice_exchange_enabled)
            self.company.with_user(
                self.manager,
            ).action_rebuild_suspend_einvoice_exchange()
            self.assertFalse(self.company.rebuild_einvoice_exchange_enabled)
            self.assertTrue(all(cron.active for cron in reception_crons))

    def test_participant_status_cron_retries_pending_approved_registration(self):
        self.company.write({
            "rebuild_einvoice_activation_approved": True,
            "account_peppol_proxy_state": "smp_registration",
        })
        self._create_production_proxy_user()
        cron = self.env.ref(
            "account_peppol.ir_cron_peppol_get_participant_status",
        )
        self.env["ir.cron.trigger"].sudo().search([
            ("cron_id", "=", cron.id),
        ]).unlink()
        scheduled_after = fields.Datetime.now()

        proxy_model = self.env.registry["account_edi_proxy_client.user"]
        with patch.object(
            proxy_model,
            "_peppol_get_participant_status",
        ) as poll_status:
            self.env["account_edi_proxy_client.user"]._cron_peppol_get_participant_status()

        self.assertTrue(poll_status.called)
        retries = self.env["ir.cron.trigger"].sudo().search([
            ("cron_id", "=", cron.id),
        ])
        self.assertEqual(len(retries), 1)
        self.assertGreaterEqual(
            retries.call_at,
            scheduled_after + timedelta(minutes=59),
        )
        self.assertLessEqual(
            retries.call_at,
            fields.Datetime.now() + timedelta(hours=1, minutes=1),
        )

    def test_participant_status_cron_does_not_retry_resolved_registration(self):
        self.company.write({
            "rebuild_einvoice_activation_approved": True,
            "account_peppol_proxy_state": "receiver",
        })
        self._create_production_proxy_user()
        cron = self.env.ref(
            "account_peppol.ir_cron_peppol_get_participant_status",
        )
        self.env["ir.cron.trigger"].sudo().search([
            ("cron_id", "=", cron.id),
        ]).unlink()

        proxy_model = self.env.registry["account_edi_proxy_client.user"]
        with patch.object(
            proxy_model,
            "_peppol_get_participant_status",
        ):
            self.env["account_edi_proxy_client.user"]._cron_peppol_get_participant_status()

        self.assertFalse(self.env["ir.cron.trigger"].sudo().search([
            ("cron_id", "=", cron.id),
        ]))

    def test_participant_status_cron_does_not_retry_unapproved_registration(self):
        proxy_users = self.env["account_edi_proxy_client.user"].sudo().search([
            ("proxy_type", "in", self.env[
                "account_edi_proxy_client.user"
            ]._get_peppol_proxy_types()),
        ])
        proxy_users.company_id.write({
            "rebuild_einvoice_activation_approved": False,
        })
        self.company.write({
            "rebuild_einvoice_activation_approved": False,
            "account_peppol_proxy_state": "smp_registration",
        })
        unapproved_proxy = self._create_production_proxy_user()
        eligible_users = self.env["account_edi_proxy_client.user"].search([
            ("company_id.rebuild_einvoice_activation_approved", "=", True),
            ("proxy_type", "in", self.env[
                "account_edi_proxy_client.user"
            ]._get_peppol_proxy_types()),
        ])
        self.assertNotIn(unapproved_proxy, eligible_users)
        cron = self.env.ref(
            "account_peppol.ir_cron_peppol_get_participant_status",
        )
        self.env["ir.cron.trigger"].sudo().search([
            ("cron_id", "=", cron.id),
        ]).unlink()

        proxy_model = self.env.registry["account_edi_proxy_client.user"]
        with patch.object(
            proxy_model,
            "_peppol_get_participant_status",
        ) as poll_status:
            self.env["account_edi_proxy_client.user"]._cron_peppol_get_participant_status()

        # The cron invokes the recordset method even when the eligible
        # recordset is empty; the domain assertion above proves exclusion.
        poll_status.assert_called_once()
        self.assertFalse(self.env["ir.cron.trigger"].sudo().search([
            ("cron_id", "=", cron.id),
        ]))

    def test_upgrade_initialization_preserves_active_production_reception(self):
        self.company.write({
            "account_peppol_contact_email": "accounting@example.invalid",
            "account_peppol_phone_number": "+33612345678",
        })
        self.company.with_user(
            self.manager,
        ).action_rebuild_run_einvoice_acceptance_test()
        self.company.rebuild_einvoice_environment = "production"
        with patch.dict(os.environ, LIVE_ENVIRONMENT, clear=False):
            self.company.with_user(
                self.manager,
            ).action_rebuild_approve_einvoice_activation()
            self._create_production_proxy_user()
            self.company.write({
                "pdp_kyc_status": "success",
                "account_peppol_proxy_state": "receiver",
            })
            self.company.with_user(
                self.manager,
            ).action_rebuild_enable_einvoice_exchange()
            active_crons = [
                self.env.ref(xmlid)
                for xmlid in (
                    "account_peppol.ir_cron_peppol_get_new_documents",
                    "account_peppol.ir_cron_peppol_get_message_status",
                    "account_peppol.ir_cron_peppol_get_participant_status",
                    "account_peppol.ir_cron_peppol_webhook_keepalive",
                    "l10n_fr_pdp.ir_cron_pdp_get_regulatory_documents",
                )
            ]
            self.env["res.company"]._rebuild_apply_default_einvoice_provider()

        self.assertTrue(self.company.rebuild_einvoice_activation_approved)
        self.assertTrue(self.company.rebuild_einvoice_exchange_enabled)
        self.assertEqual(self.company.account_peppol_proxy_state, "receiver")
        self.assertTrue(all(cron.active for cron in active_crons))
        self.assertFalse(self.company.l10n_fr_pdp_send_to_ppf)

    def test_upgrade_with_runtime_guard_disabled_preserves_onboarding_state(self):
        prepared_at = fields.Datetime.now()
        approved_at = fields.Datetime.now()
        self.company.write({
            "rebuild_einvoice_environment": "production",
            "rebuild_einvoice_production_prepared_by_id": self.manager.id,
            "rebuild_einvoice_production_prepared_at": prepared_at,
            "rebuild_einvoice_activation_approved": True,
            "rebuild_einvoice_approved_by_id": self.manager.id,
            "rebuild_einvoice_approved_at": approved_at,
            "rebuild_einvoice_exchange_enabled": True,
            "account_peppol_proxy_state": "smp_registration",
            "pdp_kyc_status": "success",
            "l10n_fr_pdp_send_to_ppf": True,
            "l10n_fr_pdp_pilot_phase": True,
        })

        with patch.dict(
            os.environ,
            {"USL_EINVOICE_LIVE_ENABLED": "0"},
            clear=False,
        ):
            self.env["res.company"]._rebuild_apply_default_einvoice_provider()

        self.assertEqual(self.company.rebuild_einvoice_environment, "production")
        self.assertEqual(
            self.company.rebuild_einvoice_production_prepared_by_id,
            self.manager,
        )
        self.assertEqual(
            self.company.rebuild_einvoice_production_prepared_at,
            prepared_at,
        )
        self.assertTrue(self.company.rebuild_einvoice_activation_approved)
        self.assertEqual(self.company.rebuild_einvoice_approved_by_id, self.manager)
        self.assertEqual(self.company.rebuild_einvoice_approved_at, approved_at)
        self.assertTrue(self.company.rebuild_einvoice_exchange_enabled)
        self.assertEqual(self.company.account_peppol_proxy_state, "smp_registration")
        self.assertEqual(self.company.pdp_kyc_status, "success")
        self.assertFalse(self.company.l10n_fr_pdp_send_to_ppf)
        self.assertFalse(self.company.l10n_fr_pdp_pilot_phase)

    def test_upgrade_initialization_preserves_current_offline_self_check(self):
        self.company.with_user(
            self.manager,
        ).action_rebuild_run_einvoice_acceptance_test()
        tested_at = self.company.rebuild_einvoice_tested_at
        fingerprint = self.company.rebuild_einvoice_test_fingerprint

        self.env["res.company"]._rebuild_apply_default_einvoice_provider()

        self.assertEqual(self.company.rebuild_einvoice_test_status, "passed")
        self.assertEqual(self.company.rebuild_einvoice_tested_at, tested_at)
        self.assertEqual(
            self.company.rebuild_einvoice_test_fingerprint,
            fingerprint,
        )
        self.assertTrue(self.company.rebuild_einvoice_test_current)
        self.assertFalse(self.company.rebuild_einvoice_exchange_enabled)
        self.assertFalse(self.company.l10n_fr_pdp_send_to_ppf)
        self.assertFalse(self.company.l10n_fr_pdp_pilot_phase)

    def test_product_registry_contains_no_migration_or_parity_menus(self):
        self.company.rebuild_einvoice_provider = False
        self.env["res.company"]._rebuild_apply_default_einvoice_provider()
        self.assertEqual(self.company.rebuild_einvoice_provider, "odoo_pdp")

        technical_tokens = (
            "import run",
            "imported",
            "migration",
            "parity",
            "reconstruction",
            "debug",
        )
        menu_model = self.env["ir.ui.menu"]
        manager_visible = menu_model.with_user(
            self.manager,
        )._visible_menu_ids()
        reviewer_visible = menu_model.with_user(
            self.reviewer,
        )._visible_menu_ids()
        module_menu_data = self.env["ir.model.data"].search([
            ("module", "=", "rebuild_account_migration"),
            ("model", "=", "ir.ui.menu"),
        ])
        technical_menus = menu_model.browse(
            module_menu_data.mapped("res_id"),
        ).filtered(
            lambda menu: any(
                token in (menu.name or "").lower()
                for token in technical_tokens
            ),
        )
        self.assertFalse(technical_menus)
        self.assertFalse(set(technical_menus.ids) & manager_visible)
        self.assertFalse(set(technical_menus.ids) & reviewer_visible)
        self.assertIn(
            self.env.ref(
                "rebuild_account_migration.menu_rebuild_einvoice_readiness",
            ).id,
            manager_visible,
        )
        settings_readiness_menu = self.env.ref(
            "rebuild_account_migration.menu_rebuild_einvoice_readiness_settings",
        )
        self.assertIn(settings_readiness_menu.id, manager_visible)
        self.assertNotIn(settings_readiness_menu.id, reviewer_visible)
        self.assertEqual(
            settings_readiness_menu.parent_id,
            self.env.ref("base.menu_users"),
        )
        self.assertEqual(
            settings_readiness_menu.action,
            self.env.ref(
                "rebuild_account_migration.action_rebuild_einvoice_readiness",
            ),
        )
        readiness_menu = self.env.ref(
            "rebuild_account_migration.menu_rebuild_einvoice_readiness",
        )
        self.assertEqual(readiness_menu.name, "Electronic Invoicing")
        self.assertEqual(
            readiness_menu.parent_id,
            self.env.ref("account.account_invoicing_menu"),
        )
        self.assertEqual(
            self.env.ref(
                "rebuild_account_migration.action_rebuild_einvoice_readiness",
            ).name,
            "Electronic Invoicing",
        )
        self.assertIn(
            self.env.ref(
                "rebuild_account_migration.menu_rebuild_einvoice_reception",
            ).id,
            reviewer_visible,
        )

    def test_roles_and_multi_company_isolation(self):
        self.company.with_user(
            self.manager,
        ).action_rebuild_run_einvoice_acceptance_test()
        source = self._source_invoice(reference="ROLE-VISIBILITY")
        payload, errors = self.env[
            "account.edi.xml.ubl_21_fr"
        ]._export_invoice(source)
        self.assertFalse(errors)
        _result, evidence, _attachment = self._import(
            payload,
            "role-visibility",
        )
        self.assertEqual(evidence.with_user(self.reviewer).status, "bill_created")
        with self.assertRaises(AccessError):
            self.company.with_user(
                self.reviewer,
            ).action_rebuild_run_einvoice_acceptance_test()
        with self.assertRaises(AccessError):
            self.env["rebuild.einvoice.reception"].with_user(
                self.reviewer,
            ).create({
                "company_id": self.company.id,
                "provider_message_uuid": "unauthorized-reviewer-write",
            })

        other_company = self.env["res.company"].create({
            "name": "Other E-Invoice Company",
        })
        other_attachment = self.env["ir.attachment"].create({
            "name": "other.xml",
            "raw": b"<Invoice/>",
            "mimetype": "application/xml",
        })
        other_evidence = self.env["rebuild.einvoice.reception"].create({
            "company_id": other_company.id,
            "provider_message_uuid": "other-company-message",
            "attachment_id": other_attachment.id,
        })
        visible = self.env["rebuild.einvoice.reception"].with_user(
            self.reviewer,
        ).search([])
        self.assertIn(evidence, visible)
        self.assertNotIn(other_evidence, visible)


@tagged(
    "post_install",
    "-at_install",
    "einvoice_browser",
)
class TestFrenchEinvoiceReceptionBrowser(
    EinvoiceTestDataMixin,
    HttpCase,
):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls._configure_company()

    def test_accounting_manager_offline_reception_journey(self):
        action = self.env.ref(
            "rebuild_account_migration.action_rebuild_einvoice_readiness",
        )
        self.start_tour(
            f"/odoo/action-{action.id}",
            "usl_einvoice_manager_reception",
            login=self.manager.login,
        )
        self.assertEqual(self.company.rebuild_einvoice_test_status, "passed")
        self.assertTrue(self.company.rebuild_einvoice_test_current)
        self.assertFalse(self.company.rebuild_einvoice_test_reception_id)

    def test_readonly_accountant_reception_visibility(self):
        evidence = self._create_persistent_received_invoice(
            "browser-readonly-reception",
        )
        action = self.env.ref(
            "rebuild_account_migration.action_rebuild_einvoice_reception",
        )
        self.start_tour(
            f"/odoo/action-{action.id}/{evidence.id}",
            "usl_einvoice_readonly_reception",
            login=self.reviewer.login,
        )
