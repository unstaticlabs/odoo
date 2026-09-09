from email.message import EmailMessage

from odoo import Command
from odoo.tests import TransactionCase, tagged

from odoo.addons.mail.tests.common import mail_new_test_user


@tagged("post_install", "-at_install", "usl_documents")
class TestDocumentIntake(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company_a = cls.env.company
        cls.company_b = cls.env["res.company"].create({"name": "Intake Second Company"})
        cls.alias = cls.env.ref("usl_documents.mail_alias_document_intake")
        if not cls.alias.alias_domain_id:
            cls.alias.alias_domain_id = cls.env["mail.alias.domain"].create({
                "name": "documents.example.invalid",
            })
        cls.sender = mail_new_test_user(
            cls.env,
            login="documents-intake-sender",
            name="Intake Sender",
            email="intake.sender@example.invalid",
            company_id=cls.company_a.id,
            company_ids=[Command.set(cls.company_a.ids)],
            groups="usl_documents.group_documents_user",
        )
        cls.employee = cls.env["hr.employee"].sudo().create({
            "name": "Intake Sender",
            "company_id": cls.company_a.id,
            "user_id": cls.sender.id,
            "work_email": "intake.sender@example.invalid",
        })
        cls.other_company_employee = cls.env["hr.employee"].sudo().create({
            "name": "Second Company Employee",
            "company_id": cls.company_b.id,
            "work_email": "second.company@example.invalid",
        })
        cls.sender_second_profile = cls.env["hr.employee"].sudo().create({
            "name": "Intake Sender",
            "company_id": cls.company_b.id,
            "user_id": cls.sender.id,
            "work_email": "intake.sender@example.invalid",
        })

    def _intake_from_email(self, sender, subject="Signed lease agreement"):
        return self.env["usl.document.intake"].sudo().message_new({
            "subject": subject,
            "email_from": sender,
            "to": self.alias.alias_full_name,
        })

    def _post_file(self, intake, name, content=b"%PDF-1.4 intake test"):
        intake.sudo().message_post(
            body="Attached file",
            subtype_xmlid="mail.mt_note",
            attachments=[(name, content)],
        )
        return intake.sudo().message_ids.attachment_ids.filtered(
            lambda attachment: attachment.name == name,
        )

    # Alias

    def test_alias_routes_mail_to_the_intake_model(self):
        self.assertEqual(self.alias.alias_name, "documents")
        self.assertEqual(self.alias.alias_model_id.model, "usl.document.intake")

    def test_alias_admits_employees_and_refuses_strangers(self):
        def error_for(address):
            message = EmailMessage()
            message["From"] = address
            return self.env["usl.document.intake"]._alias_get_error(
                message,
                {"email_from": address},
                self.alias,
            )

        self.assertFalse(error_for("Intake Sender <intake.sender@example.invalid>"))
        self.assertTrue(error_for("Stranger <stranger@example.invalid>"))

    def test_settings_publish_the_intake_address(self):
        settings = self.env["res.config.settings"].create({})

        self.assertEqual(settings.document_intake_email, self.alias.alias_full_name)

    # Intake creation

    def test_email_records_its_internal_sender(self):
        intake = self._intake_from_email(
            f"Intake Sender <{self.sender.email}>",
        )

        self.assertEqual(intake.name, "Signed lease agreement")
        self.assertEqual(intake.sender_user_id, self.sender)
        self.assertEqual(intake.company_id, self.company_a)
        self.assertTrue(intake.received_at)

    def test_email_is_owned_by_the_senders_company(self):
        intake = self._intake_from_email("second.company@example.invalid")

        self.assertEqual(intake.company_id, self.company_b)
        self.assertFalse(intake.sender_user_id)

    def test_multi_company_sender_lands_in_their_default_company(self):
        """A user holds one employee record per company they work in.

        Picking whichever record the search returned first would file the
        document in an arbitrary company, so the sender's own default company
        decides.
        """
        self.assertEqual(
            self.env["hr.employee"].sudo().search_count([
                ("user_id", "=", self.sender.id),
            ]),
            2,
        )

        intake = self._intake_from_email(f"Intake Sender <{self.sender.email}>")

        self.assertEqual(intake.company_id, self.sender.company_id)
        self.assertEqual(intake.company_id, self.company_a)

    def test_email_without_subject_is_still_named(self):
        intake = self._intake_from_email(self.sender.email, subject="   ")

        self.assertEqual(intake.name, "Emailed document")

    def test_unresolved_sender_still_captures_the_message(self):
        intake = self._intake_from_email("stranger@example.invalid")

        self.assertFalse(intake.sender_user_id)
        self.assertEqual(intake.company_id, self.env.company)

    # Archiving

    def test_intake_is_a_supported_documents_record(self):
        self.assertIn(
            "usl.document.intake",
            self.env["usl.document.link"]._allowed_models(),
        )

    def test_document_users_read_intakes_of_their_own_company_only(self):
        """Readability of the intake is what grants access to its documents.

        `usl.document._recompute_linked_record_access` permits exactly the users
        who can search the linked record, so an emailed document is visible to
        the Documents users of the sender's company and to nobody else.
        """
        own = self._intake_from_email(f"Intake Sender <{self.sender.email}>")
        other = self._intake_from_email("second.company@example.invalid")

        readable = self.env["usl.document.intake"].with_user(self.sender).search([
            ("id", "in", (own | other).ids),
        ])

        self.assertEqual(readable.ids, own.ids)

    def test_emailed_file_is_queued_as_a_library_document(self):
        intake = self._intake_from_email(f"Intake Sender <{self.sender.email}>")

        attachment = self._post_file(intake, "lease.pdf")

        self.assertEqual(attachment.res_model, "usl.document.intake")
        self.assertEqual(attachment.res_id, intake.id)
        self.assertEqual(attachment.usl_documents_origin, "chatter")
        self.assertEqual(attachment.usl_documents_archive_mode, "automatic")
        self.assertEqual(attachment.usl_documents_document_role, "library")
        self.assertEqual(
            attachment.usl_documents_policy_reason,
            "emailed_document_intake",
        )
        self.assertEqual(attachment.usl_documents_ledger_state, "pending")

        operation = self.env["usl.document.operation"].sudo().search([
            ("source_attachment_id", "=", attachment.id),
        ])
        self.assertEqual(len(operation), 1)
        self.assertEqual(operation.state, "pending")
        self.assertEqual(operation.res_model, "usl.document.intake")
        self.assertEqual(operation.res_id, intake.id)
        self.assertEqual(operation.company_id, self.company_a)
        self.assertEqual(operation.context_json["tags"], ["Email intake"])

    def test_emailed_file_paperless_cannot_read_is_not_queued(self):
        intake = self._intake_from_email(f"Intake Sender <{self.sender.email}>")

        attachment = self._post_file(intake, "newsletter.html", b"<html>hi</html>")

        self.assertEqual(attachment.usl_documents_archive_mode, "never")
        self.assertEqual(
            attachment.usl_documents_policy_reason,
            "unsupported_archive_format",
        )
        self.assertFalse(
            self.env["usl.document.operation"].sudo().search_count([
                ("source_attachment_id", "=", attachment.id),
            ]),
        )
