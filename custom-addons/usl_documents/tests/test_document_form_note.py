from lxml import etree

from odoo.tests import TransactionCase, tagged

from odoo.addons.mail.tests.common import mail_new_test_user


@tagged("post_install", "-at_install", "usl_documents")
class TestDocumentFormNote(TransactionCase):
    """The record form is read-only for archive metadata, so it must say so.

    A direct record URL (/odoo/usl.document/<id>) opens the technical form, in
    which title, correspondent, type, date and tags cannot be edited. A
    Document Administrator who followed such a link concluded that they lacked
    the rights. The form now says that it is read-only and opens the same
    document in the Documents app, where those fields are edited.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.user = mail_new_test_user(
            cls.env,
            login="usl_document_form_note_user",
            groups="base.group_user,usl_documents.group_documents_user",
            company_id=cls.company.id,
            company_ids=[cls.company.id],
        )
        cls.document = cls.env["usl.document"].create({
            "name": "Synthetic form note document",
            "paperless_id": 99101,
            "company_id": cls.company.id,
            "confidentiality": "internal",
            "review_state": "classified",
            "permission_sync_state": "synchronized",
        })

    def test_open_in_documents_selects_this_document(self):
        action = self.document.with_user(self.user).action_open_in_documents()
        self.assertEqual(action["type"], "ir.actions.client")
        self.assertEqual(action["tag"], "usl_documents.workspace")
        self.assertEqual(
            action["params"], {"initial_document_id": self.document.id},
        )

    def test_form_says_it_is_read_only_and_offers_open_in_documents(self):
        arch = self.env["usl.document"].with_user(self.user).get_views(
            [(False, "form")],
        )["views"]["form"]["arch"]
        form = etree.fromstring(arch)
        notes = form.xpath("//div[@name='archive_metadata_read_only_note']")
        self.assertEqual(len(notes), 1, "the form has no read-only note")
        self.assertIn("read-only", "".join(notes[0].itertext()))
        buttons = form.xpath(
            "//header/button[@name='action_open_in_documents'][@type='object']",
        )
        self.assertEqual(len(buttons), 1, "the header has no Open in Documents")
        self.assertEqual(buttons[0].get("string"), "Open in Documents")
        self.assertFalse(
            buttons[0].get("invisible"), "Open in Documents must always show",
        )
