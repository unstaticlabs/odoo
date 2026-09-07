from lxml import etree
from odoo.exceptions import AccessError
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("usl_project", "post_install", "-at_install")
class TestProjectCompanyDomain(TransactionCase):
    """The Company field must not offer a company that would hide the project.

    `project.project_comp_rule` filters on the *active* companies, while the
    field itself used to offer every *allowed* company. Filing a project into an
    allowed-but-inactive company therefore made it unreadable to its own author.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.main_company = cls.env.ref("base.main_company")
        cls.other_company = cls.env["res.company"].create({"name": "Inactive Co"})
        cls.user = new_test_user(
            cls.env,
            login="project.company.user@example.invalid",
            groups="base.group_user,project.group_project_user,base.group_multi_company",
            context={"no_reset_password": True},
        )
        cls.user.write({
            "company_ids": [(6, 0, (cls.main_company | cls.other_company).ids)],
            "company_id": cls.main_company.id,
        })

    def _company_field_domain(self, view_xmlid, view_type):
        arch = self.env["project.project"].with_user(self.user).get_view(
            view_id=self.env.ref(view_xmlid).id, view_type=view_type,
        )["arch"]
        nodes = etree.fromstring(arch).xpath("//field[@name='company_id'][@domain]")
        self.assertTrue(nodes, "the Company field carries no domain in %s" % view_xmlid)
        return nodes[0].get("domain")

    def test_form_company_field_is_limited_to_active_companies(self):
        self.assertEqual(
            self._company_field_domain("project.edit_project", "form"),
            "[('id', 'in', allowed_company_ids)]",
        )

    def test_list_company_field_is_limited_to_active_companies(self):
        # The list is multi_edit, so an unrestricted field loses a whole selection.
        self.assertEqual(
            self._company_field_domain("project.view_project", "list"),
            "[('id', 'in', allowed_company_ids)]",
        )

    def test_an_inactive_company_would_hide_the_project_from_its_author(self):
        """Guards the reason for the domain, not its text.

        `other_company` is allowed to the user but not active, which is exactly
        the case the domain now removes from the field.
        """
        self.assertIn(self.other_company, self.user.company_ids)

        active = self.main_company.ids
        project = self.env["project.project"].with_user(self.user).with_context(
            allowed_company_ids=active,
        ).create({"name": "Filed into an inactive company"})
        project.sudo().write({"company_id": self.other_company.id})

        with self.assertRaises(AccessError):
            project.with_user(self.user).with_context(
                allowed_company_ids=active,
            ).check_access("read")

        # Enabling the company in the switcher is what restores the record.
        project.with_user(self.user).with_context(
            allowed_company_ids=active + self.other_company.ids,
        ).check_access("read")
