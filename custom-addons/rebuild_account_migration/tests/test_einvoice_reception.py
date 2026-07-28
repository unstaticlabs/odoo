import io
import os
from unittest.mock import patch

from odoo import Command
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
            "rebuild_einvoice_provider_contract_status": "not_verified",
            "account_peppol_contact_email": False,
            "account_peppol_phone_number": False,
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
            "Test invoice reception",
        )
        self.assertIn(
            "Run the offline reception test",
            self.company.rebuild_einvoice_next_steps,
        )

        action = self.company.with_user(
            self.manager,
        ).action_rebuild_run_einvoice_acceptance_test()
        evidence = self.company.rebuild_einvoice_test_reception_id
        bill = evidence.move_id

        self.assertEqual(action["res_id"], evidence.id)
        self.assertEqual(self.company.rebuild_einvoice_test_status, "passed")
        self.assertEqual(
            self.company.rebuild_einvoice_capability_status,
            "test_passed",
        )
        self.assertTrue(evidence.is_test)
        self.assertEqual(evidence.status, "bill_created")
        self.assertEqual(evidence.document_format, "ubl")
        self.assertEqual(evidence.document_kind, "invoice")
        self.assertEqual(evidence.attempt_count, 1)
        self.assertEqual(bill.move_type, "in_invoice")
        self.assertEqual(bill.state, "draft")
        self.assertEqual(len(bill.invoice_line_ids), 2)
        self.assertAlmostEqual(bill.amount_total, 175.0, places=2)
        self.assertEqual(evidence.attachment_id.res_model, "account.move")
        self.assertEqual(evidence.attachment_id.res_id, bill.id)
        self.assertEqual(bill.ubl_cii_xml_id, evidence.attachment_id)

        self.company.write({
            "account_peppol_contact_email": "accounting@example.invalid",
            "account_peppol_phone_number": "+33612345678",
            "rebuild_einvoice_provider": "odoo_pdp",
            "rebuild_einvoice_provider_contract_status": "verified",
        })
        self.assertEqual(
            self.company.rebuild_einvoice_readiness_status,
            "ready_inactive",
        )
        self.assertEqual(
            self.company.rebuild_einvoice_next_action,
            "Continue during production deployment",
        )
        self.assertFalse(self.company.rebuild_einvoice_exchange_enabled)

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

    def test_live_boundary_provider_recovery_and_reception_only_crons(self):
        self.company.with_user(
            self.manager,
        ).action_rebuild_run_einvoice_acceptance_test()
        self.company.write({
            "account_peppol_contact_email": "accounting@example.invalid",
            "account_peppol_phone_number": "+33612345678",
            "rebuild_einvoice_provider": "odoo_pdp",
            "rebuild_einvoice_provider_contract_status": "verified",
        })
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
                self.assertIsNone(
                    live_proxy._pdp_get_regulatory_documents(),
                )
                regulatory_call.assert_not_called()
            with patch.object(
                NativePdpProxyUser,
                "_pdp_send_lifecycles",
            ) as lifecycle_call:
                self.assertIsNone(live_proxy._pdp_send_lifecycles())
                lifecycle_call.assert_not_called()
            with self.assertRaisesRegex(UserError, "E-reporting is inactive"):
                self.env["l10n.fr.pdp.reports.flow"].action_send()

            self.company.account_peppol_proxy_state = "receiver"
            self.company.with_user(
                self.manager,
            ).action_rebuild_enable_einvoice_exchange()
            reception_crons = [
                self.env.ref(xmlid)
                for xmlid in (
                    "account_peppol.ir_cron_peppol_get_new_documents",
                    "account_peppol.ir_cron_peppol_get_message_status",
                    "account_peppol.ir_cron_peppol_get_participant_status",
                    "account_peppol.ir_cron_peppol_webhook_keepalive",
                )
            ]
            restricted_crons = [
                self.env.ref(xmlid)
                for xmlid in (
                    "account_peppol_response.ir_cron_peppol_auto_register_services",
                    "l10n_fr_pdp.ir_cron_pdp_get_regulatory_documents",
                    "l10n_fr_pdp.ir_cron_pdp_send_lifecycles",
                    "l10n_fr_pdp.ir_cron_l10n_fr_pdp_generate_flows",
                )
            ]
            self.assertTrue(all(cron.active for cron in reception_crons))
            self.assertFalse(any(cron.active for cron in restricted_crons))
            self.company.with_user(
                self.manager,
            ).action_rebuild_suspend_einvoice_exchange()
            self.assertFalse(any(cron.active for cron in reception_crons))

    def test_daily_menus_hide_migration_and_parity_machinery(self):
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
        self.assertTrue(technical_menus)
        self.assertFalse(set(technical_menus.ids) & manager_visible)
        self.assertFalse(set(technical_menus.ids) & reviewer_visible)
        self.assertIn(
            self.env.ref(
                "rebuild_account_migration.menu_rebuild_einvoice_readiness",
            ).id,
            manager_visible,
        )
        readiness_menu = self.env.ref(
            "rebuild_account_migration.menu_rebuild_einvoice_readiness",
        )
        self.assertEqual(readiness_menu.name, "E-Invoicing")
        self.assertEqual(
            readiness_menu.parent_id,
            self.env.ref("account.account_invoicing_menu"),
        )
        self.assertEqual(
            self.env.ref(
                "rebuild_account_migration.action_rebuild_einvoice_readiness",
            ).name,
            "E-Invoicing",
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
        evidence = self.company.rebuild_einvoice_test_reception_id
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
        self.assertEqual(
            self.company.rebuild_einvoice_test_reception_id.move_id.state,
            "draft",
        )

    def test_readonly_accountant_reception_visibility(self):
        self.company.with_user(
            self.manager,
        ).action_rebuild_run_einvoice_acceptance_test()
        evidence = self.company.rebuild_einvoice_test_reception_id
        action = self.env.ref(
            "rebuild_account_migration.action_rebuild_einvoice_reception",
        )
        self.start_tour(
            f"/odoo/action-{action.id}/{evidence.id}",
            "usl_einvoice_readonly_reception",
            login=self.reviewer.login,
        )
