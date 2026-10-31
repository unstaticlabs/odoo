import os
import io
from pathlib import Path
from unittest import mock
import re
from PIL import Image
from functools import partial

from odoo.tests import TransactionCase, tagged, Form
from odoo.tools import BinaryBytes, frozendict
from odoo.tools.image import image_apply_opt, hex_to_rgb
from odoo.fields import Command
from odoo.exceptions import ValidationError
from odoo.tools.convert import convert_xml_import, ParseError


dir_path = os.path.dirname(os.path.realpath(__file__))
_file_cache = {}


class TestBaseDocumentLayoutHelpers(TransactionCase):
    #
    #   Public
    #
    def setUp(self):
        super(TestBaseDocumentLayoutHelpers, self).setUp()
        self.color_fields = ['primary_color', 'secondary_color']
        self.company = self.env.company
        self.css_color_error = 0
        self._set_templates_and_layouts()
        self._set_images()

    def assertColors(self, checked_obj, expected):
        _expected_getter = expected.get if isinstance(expected, dict) else partial(getattr, expected)
        for fname in self.color_fields:
            color1 = getattr(checked_obj, fname)
            color2 = _expected_getter(fname)
            if self.css_color_error:
                self._compare_colors_rgb(color1, color2)
            else:
                self.assertEqual(color1, color2)

    #
    #   Private
    #
    def _compare_colors_rgb(self, color1, color2):
        self.assertEqual(bool(color1), bool(color2))
        if not color1:
            return
        color1 = hex_to_rgb(color1)
        color2 = hex_to_rgb(color2)
        self.assertEqual(len(color1), len(color2))
        for i in range(len(color1)):
            self.assertAlmostEqual(color1[i], color2[i], delta=self.css_color_error)

    def _get_images_for_test(self):
        return ['sweden.png', 'odoo.png']

    def _set_images(self):
        for fname in self._get_images_for_test():
            fname_split = fname.split('.')
            if not fname_split[0] in _file_cache:
                with Image.open(os.path.join(dir_path, fname), 'r') as img:
                    img_bin = image_apply_opt(img, 'PNG')
                    img_val = BinaryBytes(img_bin)
                    primary, secondary = self.env['base.document.layout'].extract_image_primary_secondary_colors(img_val)
                    img_dict = frozendict({
                        'img': img_val,
                        'colors': {
                            'primary_color': primary,
                            'secondary_color': secondary,
                        },
                    })
                    _file_cache[fname_split[0]] = img_dict
        self.company_imgs = frozendict(_file_cache)

    def _set_templates_and_layouts(self):
        self.layout_template1 = self.env['ir.ui.view'].create({
            'name': 'layout_template1',
            'key': 'web.layout_template1',
            'type': 'qweb',
            'arch': '''<div></div>''',
        })
        self.env['ir.model.data'].create({
            'name': self.layout_template1.name,
            'model': 'ir.ui.view',
            'module': 'web',
            'res_id': self.layout_template1.id,
        })
        self.default_colors = {
            'primary_color': '#000000',
            'secondary_color': '#000000',
        }
        self.report_layout1 = self.env['report.layout'].create({
            'view_id': self.layout_template1.id,
            'name': 'report_%s' % self.layout_template1.name,
        })
        self.layout_template2 = self.env['ir.ui.view'].create({
            'name': 'layout_template2',
            'key': 'web.layout_template2',
            'type': 'qweb',
            'arch': '''<div></div>''',
        })
        self.env['ir.model.data'].create({
            'name': self.layout_template2.name,
            'model': 'ir.ui.view',
            'module': 'web',
            'res_id': self.layout_template2.id,
        })
        self.report_layout2 = self.env['report.layout'].create({
            'view_id': self.layout_template2.id,
            'name': 'report_%s' % self.layout_template2.name,
        })


@tagged('document_layout', "post_install", "-at_install")
class TestBaseDocumentLayout(TestBaseDocumentLayoutHelpers):
    # Logo change Tests
    def test_company_no_color_change_logo(self):
        """When neither a logo nor the colors are set
        The wizard displays the colors of the report layout
        Changing logo means the colors on the wizard change too
        Emptying the logo works and doesn't change the colors"""
        self.company.write({
            'primary_color': False,
            'secondary_color': False,
            'logo': False,
            'external_report_layout_id': self.env.ref('web.layout_template1').id,
            'paperformat_id': self.env.ref('base.paperformat_us').id,
        })
        default_colors = self.default_colors
        with Form(self.env['base.document.layout']) as doc_layout:
            self.assertColors(doc_layout, default_colors)
            self.assertEqual(doc_layout.company_id, self.company)
            doc_layout.logo = self.company_imgs['sweden']['img']

            self.assertColors(doc_layout, self.company_imgs['sweden']['colors'])

            doc_layout.logo = ''
            self.assertColors(doc_layout, self.company_imgs['sweden']['colors'])
            self.assertEqual(doc_layout.logo, '')

    def test_company_no_color_but_logo_change_logo(self):
        """When company colors are not set, but a logo is,
        the wizard displays the computed colors from the logo"""
        self.company.write({
            'primary_color': '#ff0080',
            'secondary_color': '#00ff00',
            'logo': self.company_imgs['sweden']['img'],
            'paperformat_id': self.env.ref('base.paperformat_us').id,
        })

        with Form(self.env['base.document.layout']) as doc_layout:
            self.assertColors(doc_layout, self.company)
            doc_layout.logo = self.company_imgs['odoo']['img']
            self.assertColors(doc_layout, self.company_imgs['odoo']['colors'])

    def test_company_colors_change_logo(self):
        """changes of the logo implies displaying the new computed colors"""
        self.company.write({
            'primary_color': '#ff0080',
            'secondary_color': '#00ff00',
            'logo': False,
            'paperformat_id': self.env.ref('base.paperformat_us').id,
        })

        with Form(self.env['base.document.layout']) as doc_layout:
            self.assertColors(doc_layout, self.company)
            doc_layout.logo = self.company_imgs['odoo']['img']
            self.assertColors(doc_layout, self.company_imgs['odoo']['colors'])

    def test_company_colors_and_logo_change_logo(self):
        """The colors of the company may differ from the one the logo computes
        Opening the wizard in these condition displays the company's colors
        When the logo changes, colors must change according to the logo"""
        self.company.write({
            'primary_color': '#ff0080',
            'secondary_color': '#00ff00',
            'logo': self.company_imgs['sweden']['img'],
            'paperformat_id': self.env.ref('base.paperformat_us').id,
        })

        with Form(self.env['base.document.layout']) as doc_layout:
            self.assertColors(doc_layout, self.company)
            doc_layout.logo = self.company_imgs['odoo']['img'].to_base64()
            self.assertColors(doc_layout, self.company_imgs['odoo']['colors'])

    # Layout change tests
    def test_company_colors_reset_colors(self):
        """Reset the colors when they differ from the ones originally
        computed from the company logo"""
        self.company.write({
            'primary_color': '#ff0080',
            'secondary_color': '#00ff00',
            'logo': self.company_imgs['sweden']['img'],
            'paperformat_id': self.env.ref('base.paperformat_us').id,
        })

        with Form(self.env['base.document.layout']) as doc_layout:
            self.assertColors(doc_layout, self.company)
            doc_layout.primary_color = doc_layout.logo_primary_color
            doc_layout.secondary_color = doc_layout.logo_secondary_color
            self.assertColors(doc_layout, self.company_imgs['sweden']['colors'])

    def test_parse_company_colors_grayscale(self):
        """Grayscale images with transparency - make sure the color extraction does not crash"""
        self.company.write({
            'primary_color': '#ff0080',
            'secondary_color': '#00ff00',
            'paperformat_id': self.env.ref('base.paperformat_us').id,
        })
        with Form(self.env['base.document.layout']) as doc_layout:
            with Image.open(os.path.join(dir_path, 'logo_ci.png'), 'r') as img:
                img_bin = image_apply_opt(img, 'PNG')
                doc_layout.logo = BinaryBytes(img_bin)
            self.assertNotEqual(None, doc_layout.primary_color)


    # /!\ This case is NOT supported, and probably not supportable
    # res.partner resizes manu-militari the image it is given
    # so res.company._get_logo differs from res.partner.[default image]
    # def test_company_no_colors_default_logo_and_layout_change_layout(self):
    #     """When the default YourCompany logo is set, and no colors are set on company:
    #     change wizard's color according to template"""
    #     self.company.write({
    #         'primary_color': False,
    #         'secondary_color': False,
    #         'external_report_layout_id': self.layout_template1.id,
    #     })
    #     default_colors = self.default_colors
    #     with Form(self.env['base.document.layout']) as doc_layout:
    #         self.assertColors(doc_layout, default_colors)
    #         doc_layout.report_layout_id = self.report_layout2
    #         self.assertColors(doc_layout, self.report_layout2)

    def test_company_details_blank_lines(self):
        """Test that the company address is generated dynamically using only the fields that are defined,
        without leaving any blank lines."""
        # Make sure there is no blank line in the company details.
        doc_layout_1 = self.env['base.document.layout'].create({'company_id': self.company.id})
        self.assertFalse('\n<br>\n' in doc_layout_1.company_details)

        # Make sure that 'street2' (an optional field, initially blank),
        # appears in the company details when it is defined.
        self.company.write({'street2': 'street_2_detail'})
        doc_layout_2 = self.env['base.document.layout'].create({'company_id': self.company.id})
        self.assertTrue('street_2_detail' in doc_layout_2.company_details)

    def test_extract_primary_secondary_on_svg(self):
        svg = BinaryBytes(b'<svg xmlns="http://www.w3.org/2000/svg"/>')
        primary, secondary = self.env['base.document.layout'].extract_image_primary_secondary_colors(svg)

        self.assertFalse(primary)
        self.assertFalse(secondary)

    def _get_company_report_styles(self, styles, company):
        company_marker = f'.o_company_{company.id}_layout'
        match = re.search(
            rf'{re.escape(company_marker)}.*?(?=\n\s*\.o_company_\d+_layout|\Z)',
            styles,
            re.S,
        )
        return match.group() if match else ''

    def test_report_tables_id_change_updates_company_report_styles(self):
        """Test that changing only the table design must refresh report table colors."""
        primary_color = '#ff0080'
        attachment = self.env.ref('web.asset_styles_company_report')
        self.company.write({
            'primary_color': primary_color,
            'report_tables_id': 'light',
        })
        styles = (attachment.raw or b'').decode()
        company_styles = self._get_company_report_styles(styles, self.company)
        self.assertNotIn('o_report_layout_bubble', company_styles)

        self.company.write({'report_tables_id': 'bubble'})
        styles = (attachment.raw or b'').decode()
        company_styles = self._get_company_report_styles(styles, self.company)
        self.assertIn('o_report_layout_bubble', company_styles)
        self.assertIn('thead th', company_styles)
        self.assertIn(primary_color, company_styles)


@tagged("post_install", "-at_install", "usl_upgrade_preservation")
class TestNativeXmlUpgradePreservation(TransactionCase):
    """Native XML reloads must retain configured authority and original evidence."""

    def setUp(self):
        super().setUp()
        self.Attachments = self.env["ir.attachment"]
        self.ModelData = self.env["ir.model.data"]
        self.reviewer = self.env["res.users"].with_context(no_reset_password=True).create({
            "name": "Synthetic upgrade reviewer",
            "login": "native-loader-reviewer@example.invalid",
            "group_ids": [Command.clear()],
        })

    def _bind(self, xmlid, record):
        module, name = xmlid.split(".", 1)
        binding = self.ModelData.search([("module", "=", module), ("name", "=", name)])
        values = {"model": record._name, "res_id": record.id, "noupdate": False}
        if binding:
            binding.write(values)
        else:
            binding = self.ModelData.create({"module": module, "name": name, **values})
        binding.flush_recordset(["model", "res_id", "noupdate"])
        self.env.registry.clear_cache()
        return binding

    def _import(self, module, xmlid, model, values, *, mode="update"):
        from xml.sax.saxutils import escape, quoteattr

        fields = []
        for name, value in values.items():
            if name == "raw":
                fields.append(f'<field name="raw" eval={quoteattr(repr(value))}/>')
            elif name == "user_ids":
                commands = [(int(command[0]), *command[1:]) for command in value]
                fields.append(f'<field name="user_ids" eval={quoteattr(repr(commands))}/>')
            else:
                fields.append(f'<field name={quoteattr(name)}>{escape(str(value))}</field>')
        document = io.BytesIO((
            f'<odoo><record id={quoteattr(xmlid)} model={quoteattr(model)}>'
            + "".join(fields) + '</record></odoo>'
        ).encode())
        document.name = "synthetic-upgrade-preservation.xml"
        convert_xml_import(self.env, module, document, mode=mode)

    def _group(self, xmlid="stock.group_stock_manager"):
        group = self.env["res.groups"].create({
            "name": "Synthetic original authority",
            "user_ids": [Command.link(self.env.uid)],
        })
        self._bind(xmlid, group)
        return group

    def _style(self, **overrides):
        attachment = self.Attachments.create({
            "name": "res.company.scss", "type": "binary", "mimetype": "text/scss",
            "url": "web/static/asset_styles_company_report.scss",
            "raw": BinaryBytes(b".synthetic-original { color: black; }"),
            **overrides,
        })
        self._bind("web.asset_styles_company_report", attachment)
        return attachment

    def _reload_style(self, **overrides):
        self._import("web", "asset_styles_company_report", "ir.attachment", {
            "name": "res.company.scss", "type": "binary", "mimetype": "text/scss",
            "url": "web/static/asset_styles_company_report.scss",
            "raw": b".synthetic-current { color: blue; }", **overrides,
        })

    def _attachment_row(self, attachment):
        self.env.flush_all()
        self.env.cr.execute("SELECT to_jsonb(a) FROM ir_attachment a WHERE id=%s", [attachment.id])
        return self.env.cr.fetchone()[0]

    def _attachment_ids(self):
        return set(self.Attachments.search([]).ids)

    def test_existing_native_seed_reload_keeps_members_and_updates_other_fields(self):
        group = self._group()
        members = group.user_ids
        self._import("stock", "group_stock_manager", "res.groups", {
            "name": "Synthetic updated native label",
            "user_ids": [Command.link(self.reviewer.id)],
        })
        self.assertEqual(group.name, "Synthetic updated native label")
        self.assertEqual(group.user_ids, members)

    def test_new_native_seed_group_keeps_install_membership_behavior(self):
        self.ModelData.search([
            ("module", "=", "stock"), ("name", "=", "group_stock_manager"),
        ]).unlink()
        self._import("stock", "group_stock_manager", "res.groups", {
            "name": "Synthetic new native group",
            "user_ids": [Command.link(self.reviewer.id)],
        })
        self.assertIn(self.reviewer, self.env.ref("stock.group_stock_manager").user_ids)

    def test_init_mode_keeps_native_seed_behavior(self):
        group = self._group()
        self._import("stock", "group_stock_manager", "res.groups", {
            "user_ids": [Command.link(self.reviewer.id)],
        }, mode="init")
        self.assertIn(self.reviewer, group.user_ids)

    def test_unreviewed_group_and_ordinary_write_are_unchanged(self):
        xmlid = "web.usl_upgrade_unreviewed_group"
        group = self._group(xmlid)
        self._import("web", xmlid, "res.groups", {
            "user_ids": [Command.link(self.reviewer.id)],
        })
        self.assertIn(self.reviewer, group.user_ids)
        reviewed = self._group()
        reviewed.write({"user_ids": [Command.link(self.reviewer.id)]})
        self.assertIn(self.reviewer, reviewed.user_ids)

    def test_custom_explicit_xml_migration_can_change_native_membership(self):
        if self.env["ir.module.module"].search_count([
            ("name", "=", "stock"), ("state", "=", "installed"),
        ]) != 1:
            self.skipTest("Cross-module XMLID fixture requires the installed Stock closure")
        group = self._group()
        self._import("web", "stock.group_stock_manager", "res.groups", {
            "user_ids": [Command.link(self.reviewer.id)],
        })
        self.assertIn(self.reviewer, group.user_ids)

    def test_report_style_reload_versions_bytes_and_keeps_original_row_and_blob(self):
        original = self._style()
        original_row = self._attachment_row(original)
        original_bytes = original.raw.content
        original_path = Path(original._full_path(original.store_fname)) if original.store_fname else None
        # Warm XMLID lookup so rebinding also proves cache invalidation.
        self.assertEqual(self.env.ref("web.asset_styles_company_report"), original)
        self._reload_style()
        current = self.env.ref("web.asset_styles_company_report")
        self.assertNotEqual(current, original)
        self.assertEqual(current.raw.content, b".synthetic-current { color: blue; }")
        self.assertEqual(self._attachment_row(original), original_row)
        self.assertEqual(original.raw.content, original_bytes)
        if original_path:
            self.assertEqual(original_path.read_bytes(), original_bytes)
        self.assertEqual(self.Attachments._get_serve_attachment(current.url), current)

    def test_repeated_identical_style_reload_does_not_create_another_version(self):
        self._style()
        self._reload_style()
        current = self.env.ref("web.asset_styles_company_report")
        attachments = self._attachment_ids()
        self._reload_style()
        self.assertEqual(self.env.ref("web.asset_styles_company_report"), current)
        self.assertEqual(self._attachment_ids(), attachments)

    def test_new_style_and_init_mode_keep_native_loading(self):
        self.ModelData.search([
            ("module", "=", "web"), ("name", "=", "asset_styles_company_report"),
        ]).unlink()
        self._reload_style()
        current = self.env.ref("web.asset_styles_company_report")
        self.assertEqual(current.raw.content, b".synthetic-current { color: blue; }")
        self._import("web", "asset_styles_company_report", "ir.attachment", {
            "raw": b".synthetic-init { color: green; }",
        }, mode="init")
        self.assertEqual(self.env.ref("web.asset_styles_company_report"), current)
        self.assertEqual(current.raw.content, b".synthetic-init { color: green; }")

    def test_dangling_generated_style_binding_is_recreated_natively(self):
        original = self._style()
        self.assertEqual(self.env.ref("web.asset_styles_company_report"), original)
        self.env.flush_all()
        self.env.cr.execute("DELETE FROM ir_attachment WHERE id=%s", [original.id])
        self.Attachments.invalidate_model()
        self._reload_style()
        current = self.env.ref("web.asset_styles_company_report")
        self.assertNotEqual(current.id, original.id)
        self.assertEqual(current.raw.content, b".synthetic-current { color: blue; }")
        self.assertEqual(self.Attachments._get_serve_attachment(current.url), current)

    def test_noupdate_style_binding_keeps_native_skip_behavior(self):
        original = self._style()
        binding = self.ModelData.search([
            ("module", "=", "web"), ("name", "=", "asset_styles_company_report"),
        ])
        binding.noupdate = True
        row = self._attachment_row(original)
        attachments = self._attachment_ids()
        self._reload_style()
        self.assertEqual(self.env.ref("web.asset_styles_company_report"), original)
        self.assertEqual(self._attachment_ids(), attachments)
        self.assertEqual(self._attachment_row(original), row)

    def test_outer_rollback_and_native_failure_cleanup_restore_style_binding(self):
        class AbortFixture(Exception):
            pass

        original = self._style()
        row = self._attachment_row(original)
        attachments = self._attachment_ids()
        with self.assertRaises(AbortFixture), self.env.cr.savepoint():
            self._reload_style()
            self.assertNotEqual(self.env.ref("web.asset_styles_company_report"), original)
            raise AbortFixture()
        # XMLID ormcache is registry-owned, not savepoint-owned. This is the
        # native service failure cleanup after a surrounding transaction abort.
        self.env.registry.reset_changes()
        self.assertEqual(self.env.ref("web.asset_styles_company_report"), original)
        self.assertEqual(self._attachment_ids(), attachments)
        self.assertEqual(self._attachment_row(original), row)

    def test_business_attachment_xml_and_ordinary_style_write_are_unchanged(self):
        attachment = self.Attachments.create({
            "name": "Synthetic business evidence", "raw": BinaryBytes(b"original"),
            "res_model": "res.partner", "res_id": self.env.company.partner_id.id,
        })
        self._bind("web.usl_upgrade_business_attachment", attachment)
        self._import("web", "usl_upgrade_business_attachment", "ir.attachment", {"raw": b"updated"})
        self.assertEqual(self.env.ref("web.usl_upgrade_business_attachment"), attachment)
        self.assertEqual(attachment.raw.content, b"updated")
        style = self._style()
        style.write({"raw": BinaryBytes(b"ordinary intentional edit")})
        self.assertEqual(self.env.ref("web.asset_styles_company_report"), style)
        self.assertEqual(style.raw.content, b"ordinary intentional edit")

    def test_wrong_asset_model_binding_fails_without_creating_or_modifying_attachments(self):
        self._bind("web.asset_styles_company_report", self.env.company.partner_id)
        attachments = self._attachment_ids()
        with self.assertRaises(ParseError):
            self._reload_style()
        self.assertEqual(self._attachment_ids(), attachments)
        self.assertEqual(self.env.ref("web.asset_styles_company_report"), self.env.company.partner_id)

    def test_malformed_existing_style_provenance_fails_before_any_effect(self):
        for malformed in (
            {"name": "Synthetic unrelated evidence"},
            {"type": "url"},
            {"url": "synthetic/unrelated.css"},
            {"mimetype": "text/plain"},
            {"res_model": "res.partner", "res_id": self.env.company.partner_id.id},
        ):
            with self.subTest(fields=sorted(malformed)), self.env.cr.savepoint():
                original = self._style(**malformed)
                row = self._attachment_row(original)
                attachments = self._attachment_ids()
                with self.assertRaises(ParseError):
                    self._reload_style()
                self.assertEqual(self._attachment_ids(), attachments)
                self.assertEqual(self._attachment_row(original), row)
                self.assertEqual(self.env.ref("web.asset_styles_company_report"), original)

    def test_malformed_incoming_style_metadata_fails_before_any_effect(self):
        original = self._style()
        row = self._attachment_row(original)
        attachments = self._attachment_ids()
        with self.assertRaises(ParseError):
            self._reload_style(res_model="res.partner", res_id=str(self.env.company.partner_id.id))
        self.assertEqual(self._attachment_ids(), attachments)
        self.assertEqual(self._attachment_row(original), row)
        self.assertEqual(self.env.ref("web.asset_styles_company_report"), original)

    def test_failed_normal_style_loader_rolls_back_derivative_and_xmlid_together(self):
        original = self._style()
        row = self._attachment_row(original)
        attachments = self._attachment_ids()
        with mock.patch.object(type(self.Attachments), "_load_records", side_effect=ValidationError("Synthetic failure")):
            with self.assertRaises(ParseError):
                self._reload_style()
        self.assertEqual(self.env.ref("web.asset_styles_company_report"), original)
        self.assertEqual(self._attachment_ids(), attachments)
        self.assertEqual(self._attachment_row(original), row)
