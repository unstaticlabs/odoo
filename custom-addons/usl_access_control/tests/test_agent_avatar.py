"""The Agent form lets its manager set the Agent's picture, and nobody else."""

import base64
import io

from PIL import Image

from odoo import Command
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, tagged
from odoo.tests.common import new_test_user


def _placeholder_png(colour):
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), colour).save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode()


@tagged("post_install", "-at_install", "usl_agent_avatar")
class TestAgentAvatar(TransactionCase):
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
