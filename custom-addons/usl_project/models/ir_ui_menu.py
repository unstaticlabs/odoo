from odoo import models


FAVORITE_PROJECT_MENU_LIMIT = 12


class IrUiMenu(models.Model):
    _inherit = "ir.ui.menu"

    def load_web_menus(self, debug):
        menus = super().load_web_menus(debug)
        project_app = self.env.ref("project.menu_main_pm", raise_if_not_found=False)
        if not project_app or project_app.id not in menus:
            return menus

        project_app_menu = menus[project_app.id]
        redundant_menu_ids = {
            menu.id
            for xmlid in ("project.menu_projects", "project.menu_projects_group_stage")
            if (menu := self.env.ref(xmlid, raise_if_not_found=False))
        }
        project_app_menu["children"] = [
            menu_id
            for menu_id in project_app_menu["children"]
            if menu_id not in redundant_menu_ids
        ]

        favorite_projects = self.env["project.project"].search(
            [
                ("favorite_user_ids", "in", self.env.uid),
                ("is_template", "=", False),
            ],
            order="sequence, name, id",
            limit=FAVORITE_PROJECT_MENU_LIMIT,
        )
        favorite_menu_ids = []
        task_action = self.env.ref('project.act_project_project_2_project_task_all')
        for project in favorite_projects:
            menu_id = f"usl-project-favorite-{project.id}"
            favorite_menu_ids.append(menu_id)
            menus[menu_id] = {
                "id": menu_id,
                "name": project.name,
                "children": [],
                "appID": project_app.id,
                "xmlid": "",
                "actionID": {
                    "type": "ir.actions.client",
                    "tag": "usl_project_favorite_tasks",
                    "name": project.name,
                    "res_id": project.id,
                },
                "actionPath": f"project/{project.id}/action-{task_action.id}",
                "actionModel": "ir.actions.client",
                "webIcon": False,
                "webIconData": False,
                "webIconDataMimetype": False,
            }
        secondary_menu_ids = {
            menu.id
            for xmlid in ("project.menu_project_report", "project.menu_project_config")
            if (menu := self.env.ref(xmlid, raise_if_not_found=False))
        }
        insertion_index = next(
            (
                index
                for index, menu_id in enumerate(project_app_menu["children"])
                if menu_id in secondary_menu_ids
            ),
            len(project_app_menu["children"]),
        )
        project_app_menu["children"][insertion_index:insertion_index] = favorite_menu_ids
        return menus
