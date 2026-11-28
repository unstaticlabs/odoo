from odoo import Command
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("post_install", "-at_install", "usl_documents_b2c")
class TestB2cDocumentContexts(TransactionCase):
    def test_b2c_business_records_support_archived_documents(self):
        allowed = self.env["usl.document.link"]._allowed_models()
        for model_name in (
            "b2c.order",
            "b2c.payment.event",
            "b2c.fulfilment.event",
            "b2c.accounting.session",
        ):
            self.assertIn(model_name, allowed)
            self.assertIn("archived_document_count", self.env[model_name]._fields)

    def _company_sessions(self):
        source = self.env.company
        target = self.env["res.company"].create({"name": "Synthetic archive destination"})
        operator = new_test_user(
            self.env, login="qa.archived.session.operator",
            groups="base.group_user,base.group_multi_company,usl_b2c.group_b2c_operator",
            company_id=source.id, company_ids=[Command.set((source | target).ids)],
        )
        sessions = self.env["b2c.accounting.session"].with_user(operator).with_context(
            allowed_company_ids=(source | target).ids,
        )
        session = sessions.create({"period_start": "2033-03-01", "company_id": source.id})
        return source, target, session

    def _archived_document(self, company):
        return self.env["usl.document"].create({
            "name": "Synthetic supporting archive", "paperless_id": 901031,
            "company_id": company.id, "review_state": "classified",
        })

    def test_archive_only_session_keeps_its_company_and_relationship(self):
        source, target, session = self._company_sessions()
        document = self._archived_document(source)
        link = self.env["usl.document.link"].with_context(usl_documents_defer_access_sync=True).create_for_record(
            document, session._name, session.id,
        )
        self.assertFalse(session.accounting_link_ids)
        with self.assertRaisesRegex(UserError, "linked accounting evidence"):
            session.write({"company_id": target.id})
        self.assertEqual(session.company_id, source)
        self.assertEqual(link.company_id, source)
        self.assertEqual(document.company_id, source)
        self.assertEqual(link.res_id, session.id)
        unlinked = session.create({"period_start": "2033-04-01", "company_id": source.id})
        unlinked.write({"company_id": target.id})
        self.assertEqual(unlinked.company_id, target)

    def test_document_manager_with_session_read_can_link_to_locked_session(self):
        source, _target, session = self._company_sessions()
        session.sudo().write({"state": "locked"})
        document = self._archived_document(source)
        reviewer = new_test_user(
            self.env, login="qa.archived.session.reader",
            groups="base.group_user,usl_b2c.group_b2c_reader,usl_documents.group_documents_manager",
            company_id=source.id, company_ids=[Command.set(source.ids)],
        )
        reader_session = session.with_user(reviewer).with_context(allowed_company_ids=source.ids)
        self.assertTrue(reader_session.has_access("read"))
        self.assertFalse(reader_session.has_access("write"))
        reader_document = document.with_env(reader_session.env)
        link = reader_session.env["usl.document.link"].with_context(usl_documents_defer_access_sync=True).create_for_record(
            reader_document, session._name, session.id,
        )
        self.assertEqual(link.linked_by_id, reviewer)
        self.assertEqual(reader_session.write_uid, reviewer)
        self.assertEqual(session.state, "locked")

    def test_provider_evidence_document_is_restricted_and_readonly(self):
        field = self.env["b2c.provider.evidence"]._fields["archived_document_id"]
        self.assertTrue(field.readonly)
        self.assertEqual(field.ondelete, "restrict")
        self.assertEqual(field.groups, "usl_b2c.group_b2c_sensitive_evidence")

    def test_forms_have_one_contextual_documents_entry_point(self):
        for xmlid in (
            "usl_documents_b2c.view_b2c_order_documents",
            "usl_documents_b2c.view_b2c_payment_event_documents",
            "usl_documents_b2c.view_b2c_fulfilment_event_documents",
            "usl_documents_b2c.view_b2c_accounting_session_documents",
        ):
            arch = self.env.ref(xmlid).arch_db
            self.assertEqual(arch.count('name="action_open_documents_workspace"'), 2)
            self.assertIn('string="Upload"', arch)
            self.assertIn('string="Documents"', arch)
