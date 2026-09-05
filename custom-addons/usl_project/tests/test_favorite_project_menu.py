from odoo import Command
from odoo.tests import TransactionCase, new_test_user, tagged


@tagged("usl_project", "post_install", "-at_install")
class TestFavoriteProjectMenu(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.project_user = new_test_user(
            cls.env,
            login="favorite.project.user@example.invalid",
            groups="base.group_user,project.group_project_user",
            context={"no_reset_password": True},
        )

    def _project_children(self, user, company_ids):
        menus = self.env["ir.ui.menu"].with_user(user).with_context(
            allowed_company_ids=company_ids,
        ).load_web_menus(False)
        project_app_id = self.env.ref("project.menu_main_pm").id
        return menus, menus[project_app_id]["children"]

    def test_menu_shows_only_readable_active_non_template_favorites(self):
        favorite = self.env["project.project"].create({
            "name": "Customer rollout",
            "privacy_visibility": "employees",
            "sequence": 5,
        })
        private_favorite = self.env["project.project"].create({
            "name": "Private leadership project",
            "privacy_visibility": "followers",
        })
        archived_favorite = self.env["project.project"].create({
            "name": "Archived project",
            "privacy_visibility": "employees",
            "active": False,
        })
        template_favorite = self.env["project.project"].create({
            "name": "Template project",
            "privacy_visibility": "employees",
            "is_template": True,
        })
        self.project_user.favorite_project_ids = (
            favorite | private_favorite | archived_favorite | template_favorite
        )

        menus, children = self._project_children(
            self.project_user,
            [self.env.company.id],
        )
        favorite_menu_id = f"usl-project-favorite-{favorite.id}"

        self.assertIn(favorite_menu_id, children)
        self.assertNotIn(f"usl-project-favorite-{private_favorite.id}", children)
        self.assertNotIn(f"usl-project-favorite-{archived_favorite.id}", children)
        self.assertNotIn(f"usl-project-favorite-{template_favorite.id}", children)
        self.assertNotIn(self.env.ref("project.menu_projects").id, children)
        self.assertNotIn(self.env.ref("project.menu_projects_group_stage").id, children)
        primary_menu = self.env.ref("project.menu_project_management")
        if primary_menu.id in children:
            self.assertLess(children.index(primary_menu.id), children.index(favorite_menu_id))
        for xmlid in ("project.menu_project_report", "project.menu_project_config"):
            secondary_menu = self.env.ref(xmlid)
            if secondary_menu.id in children:
                self.assertLess(children.index(favorite_menu_id), children.index(secondary_menu.id))
        self.assertEqual(menus[favorite_menu_id]["actionPath"], f"project.project/{favorite.id}")
        self.assertEqual(menus[favorite_menu_id]["actionID"], {
            "type": "ir.actions.client",
            "tag": "project_top_menu_overview",
            "name": favorite.name,
            "res_id": favorite.id,
        })

    def test_menu_follows_selected_companies_and_favorite_changes(self):
        other_company = self.env["res.company"].create({"name": "Other company"})
        self.project_user.company_ids = [Command.link(other_company.id)]
        current_project = self.env["project.project"].create({
            "name": "Current company project",
            "company_id": self.env.company.id,
            "privacy_visibility": "employees",
        })
        other_project = self.env["project.project"].with_company(other_company).create({
            "name": "Other company project",
            "company_id": other_company.id,
            "privacy_visibility": "employees",
        })
        self.project_user.favorite_project_ids = current_project | other_project

        _, current_children = self._project_children(
            self.project_user,
            [self.env.company.id],
        )
        _, other_children = self._project_children(
            self.project_user,
            [other_company.id],
        )

        self.assertIn(f"usl-project-favorite-{current_project.id}", current_children)
        self.assertNotIn(f"usl-project-favorite-{other_project.id}", current_children)
        self.assertIn(f"usl-project-favorite-{other_project.id}", other_children)
        self.assertNotIn(f"usl-project-favorite-{current_project.id}", other_children)

        self.project_user.favorite_project_ids -= current_project
        _, reloaded_children = self._project_children(
            self.project_user,
            [self.env.company.id],
        )
        self.assertNotIn(f"usl-project-favorite-{current_project.id}", reloaded_children)

    def test_menu_caps_a_large_favorite_set_with_a_bounded_query(self):
        projects = self.env["project.project"].create([
            {
                "name": f"Favorite {index:02d}",
                "privacy_visibility": "employees",
                "sequence": index,
            }
            for index in range(200)
        ])
        self.project_user.favorite_project_ids = projects
        self.env.cr.flush()
        query_count_before = self.env.cr.sql_log_count

        menus, children = self._project_children(
            self.project_user,
            [self.env.company.id],
        )

        query_count = self.env.cr.sql_log_count - query_count_before
        favorite_menu_ids = [
            menu_id
            for menu_id in children
            if isinstance(menu_id, str) and menu_id.startswith("usl-project-favorite-")
        ]
        # Loading the native menu tree has a stable base cost.  The favorite
        # lookup stays one capped search and must not grow with the number of
        # favorites attached to the user.
        self.assertLessEqual(query_count, 50)
        self.assertEqual(len(favorite_menu_ids), 12)
        self.assertEqual(
            [menus[menu_id]["name"] for menu_id in favorite_menu_ids],
            [f"Favorite {index:02d}" for index in range(12)],
        )
        self.assertEqual(
            self.env["project.project"].with_user(self.project_user).search_count([
                ("favorite_user_ids", "in", self.project_user.id),
                ("is_template", "=", False),
            ]),
            200,
        )
