"""The Agent form lets its manager set the Agent's picture, and nobody else."""

import base64
import io

from PIL import Image

from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged
from odoo.tests.common import new_test_user


def _placeholder_png(colour):
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), colour).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


class _AvatarCase(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.company = cls.env.company
        cls.manager = new_test_user(
            cls.env,
            login="qa.avatar.manager",
            groups="base.group_user",
            company_id=cls.company.id,
            company_ids=[Command.set(cls.company.ids)],
            usl_identity_classification="active",
        )
        cls.stranger = new_test_user(
            cls.env,
            login="qa.avatar.stranger",
            groups="base.group_user",
            company_id=cls.company.id,
            company_ids=[Command.set(cls.company.ids)],
            usl_identity_classification="active",
        )
        cls.agent = cls.env["usl.agent"].with_user(cls.manager).create(
            {
                "name": "[QA avatar] Agent",
                "purpose": "Synthetic Agent for the avatar form field.",
                "company_id": cls.company.id,
                "company_ids": [Command.set(cls.company.ids)],
            },
        )


@tagged("post_install", "-at_install", "usl_agent_avatar")
class TestAgentAvatar(_AvatarCase):
    def test_manager_sets_the_agent_picture_without_user_rights(self):
        picture = _placeholder_png("#3366cc")
        self.assertFalse(
            self.manager.has_group("base.group_erp_manager")
            or self.manager.has_group("base.group_system"),
        )

        self.agent.write({"image_1920": picture})

        backing_user = self.agent.user_id.sudo()
        self.assertTrue(backing_user.image_1920)
        self.assertEqual(self.agent.image_1920.content, backing_user.image_1920.content)
        self.assertTrue(self.agent.image_128)

    def test_stranger_cannot_read_or_change_the_agent_picture(self):
        self.agent.write({"image_1920": _placeholder_png("#3366cc")})
        before = self.agent.user_id.sudo().image_1920
        foreign = self.agent.with_user(self.stranger)

        with self.assertRaises(AccessError):
            foreign.write({"image_1920": _placeholder_png("#cc3333")})
        with self.assertRaises(AccessError):
            foreign.image_1920  # noqa: B018
        self.assertEqual(self.agent.user_id.sudo().image_1920.content, before.content)

    def test_replacing_and_clearing_the_picture_needs_no_irreversible_permission(self):
        self.agent.write({"image_1920": _placeholder_png("#3366cc")})
        self.agent.write({"image_1920": _placeholder_png("#cc3333")})
        self.agent.write({"image_1920": False})
        self.assertFalse(self.agent.user_id.sudo().image_1920)
        self.assertFalse(self.agent.image_1920)

    def test_form_view_offers_an_editable_image_widget_to_the_manager(self):
        from lxml import etree

        view = self.agent.with_user(self.manager).get_view(view_type="form")
        arch = etree.fromstring(view["arch"])
        fields_ = arch.xpath("//field[@name='image_1920']")

        self.assertEqual(len(fields_), 1)
        self.assertEqual(fields_[0].get("widget"), "image")
        self.assertNotIn("readonly", fields_[0].attrib)


_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" width="8" height="8">'
    b'<script>alert(1)</script></svg>'
)
_HTML = b"<html><body><script>alert(1)</script></body></html>"


def _b64(raw):
    return base64.b64encode(raw).decode()


def _picture(fmt, colour="#3366cc"):
    Image.init()  # Pillow registers its encoders lazily
    buffer = io.BytesIO()
    Image.new("RGB", (32, 32), colour).save(buffer, format=fmt)
    return buffer.getvalue()


@tagged("post_install", "-at_install", "usl_agent_avatar")
class TestAgentAvatarActiveContent(_AvatarCase):
    """An owner without administrator rights cannot store active content."""

    def _backing_picture(self):
        return self.agent.user_id.sudo().image_1920

    def _unchanged(self, before):
        after = self._backing_picture()
        self.assertEqual(bool(after), bool(before))
        if before:
            self.assertEqual(after.content, before.content)

    def _refused_payloads(self):
        return {
            "svg": _SVG,
            "svg with xml prolog": b'<?xml version="1.0"?>' + _SVG,
            "html": _HTML,
            "plain text": b"not an image at all",
            "pdf": b"%PDF-1.4\n1 0 obj<<>>endobj\n",
            "truncated png": _picture("PNG")[:20],
            "tiff header": b"II*\x00\x08\x00\x00\x00" + bytes(32),
            "png header then svg": _picture("PNG")[:8] + _SVG,
        }

    def test_owner_cannot_write_active_content_through_write(self):
        self.agent.write({"image_1920": _b64(_picture("PNG"))})
        before = self._backing_picture()
        self.assertFalse(self.manager.has_group("base.group_system"))
        for label, payload in self._refused_payloads().items():
            with self.subTest(label), self.assertRaises(UserError):
                with self.cr.savepoint():
                    self.agent.write({"image_1920": _b64(payload)})
            self._unchanged(before)

    def test_owner_cannot_write_active_content_through_create(self):
        for label, payload in self._refused_payloads().items():
            with self.subTest(label), self.assertRaises(UserError):
                with self.cr.savepoint():
                    self.env["usl.agent"].with_user(self.manager).create(
                        {
                            "name": f"[QA avatar] {label}",
                            "purpose": "Synthetic Agent refused at creation.",
                            "company_id": self.company.id,
                            "company_ids": [Command.set(self.company.ids)],
                            "image_1920": _b64(payload),
                        },
                    )

    def test_owner_cannot_write_active_content_through_rpc_entry_points(self):
        # web_save is what the form sends, and a crafted call can also reach
        # the ORM through write on a recordset or the image_128 sibling.
        self.agent.write({"image_1920": _b64(_picture("PNG"))})
        before = self._backing_picture()
        owned = self.agent.with_user(self.manager)
        with self.assertRaises(UserError), self.cr.savepoint():
            owned.web_save({"image_1920": _b64(_SVG)}, {})
        self._unchanged(before)
        owned.write({"image_128": _b64(_SVG)})
        self._unchanged(before)
        self.assertNotIn(b"<svg", bytes(self._backing_picture().content))

    def test_refused_svg_is_not_stored_even_when_the_owner_replaces_a_picture(self):
        self.agent.write({"image_1920": _b64(_picture("PNG"))})
        before = self._backing_picture()
        with self.assertRaises(UserError), self.cr.savepoint():
            self.agent.write({"image_1920": _b64(_SVG)})
        attachments = self.env["ir.attachment"].sudo().search(
            [
                ("res_model", "=", "res.users"),
                ("res_id", "=", self.agent.user_id.id),
                ("res_field", "=", "image_1920"),
            ],
        )
        self.assertFalse(attachments.filtered(lambda a: a.mimetype == "image/svg+xml"))
        self._unchanged(before)

    def test_owner_still_sets_every_accepted_raster_format(self):
        for fmt, mimetype in (
            ("PNG", "image/png"),
            ("JPEG", "image/jpeg"),
            ("GIF", "image/gif"),
            ("WEBP", "image/webp"),
        ):
            Image.init()
            if fmt not in Image.SAVE:
                continue  # this Pillow build cannot encode the format
            with self.subTest(fmt):
                self.agent.write({"image_1920": _b64(_picture(fmt))})
                self.assertTrue(self._backing_picture())
                self.assertEqual(self._backing_picture().mimetype, mimetype)
        self.agent.write({"image_1920": False})
        self.assertFalse(self._backing_picture())

    def test_stranger_is_denied_before_the_picture_is_looked_at(self):
        before = self._backing_picture()
        foreign = self.agent.with_user(self.stranger)
        for payload in (_picture("PNG"), _SVG):
            with self.assertRaises(AccessError), self.cr.savepoint():
                foreign.write({"image_1920": _b64(payload)})
        self._unchanged(before)

    def test_administrator_keeps_what_native_odoo_allows_on_users(self):
        admin = new_test_user(
            self.env,
            login="qa.avatar.admin",
            groups="base.group_user,base.group_system",
            company_id=self.company.id,
            company_ids=[Command.set(self.company.ids)],
            usl_identity_classification="active",
        )
        self.agent.with_user(admin).write({"image_1920": _b64(_SVG)})
        self.assertTrue(self._backing_picture())
