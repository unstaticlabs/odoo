# -*- coding: utf-8 -*-
# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import models


class IrUiMenu(models.Model):
    _inherit = 'ir.ui.menu'

    def load_web_menus(self, debug):
        menus = super().load_web_menus(debug)
        project_menu = self.env.ref('project.menu_main_pm', raise_if_not_found=False)
        if not project_menu or project_menu.id not in menus:
            return menus

        project_menu_data = menus[project_menu.id]
        project_menu_ids = {
            menu.id
            for xmlid in ('project.menu_projects', 'project.menu_projects_group_stage')
            if (menu := self.env.ref(xmlid, raise_if_not_found=False))
        }
        project_menu_data['children'] = [
            menu_id for menu_id in project_menu_data['children']
            if menu_id not in project_menu_ids
        ]

        favorite_projects = self.env['project.project'].search([
            ('id', 'in', self.env.user.favorite_project_ids.ids),
            ('is_template', '=', False),
        ], order='sequence, name, id')
        favorite_menu_ids = []
        for project in favorite_projects:
            menu_id = f'project-{project.id}'
            favorite_menu_ids.append(menu_id)
            menus[menu_id] = {
                'id': menu_id,
                'name': project.name,
                'children': [],
                'appID': project_menu.id,
                'xmlid': '',
                'actionID': {
                    'type': 'ir.actions.client',
                    'tag': 'project_top_menu_overview',
                    'name': project.name,
                    'res_id': project.id,
                },
                # The browser intercepts menu clicks and executes actionID. This
                # fallback keeps a new-tab navigation on the Project app's
                # standard entry point rather than producing an invalid URL.
                'actionPath': 'project',
                'actionModel': 'ir.actions.client',
                'webIcon': False,
                'webIconData': False,
                'webIconDataMimetype': False,
            }
        project_menu_data['children'] = favorite_menu_ids + project_menu_data['children']
        return menus

    def _load_menus_blacklist(self):
        res = super()._load_menus_blacklist()
        if not self.env.user.has_group('project.group_project_manager'):
            res.append(self.env.ref('project.rating_rating_menu_project').id)
        if self.env.user.has_group('project.group_project_stages'):
            res.append(self.env.ref('project.menu_projects').id)
            res.append(self.env.ref('project.menu_projects_config').id)
        if not (
            self.env.user.has_group('project.group_project_stages') and
            self.env.user.has_group('base.group_no_one')
        ):
            res.append(self.env.ref('project.menu_project_config_project_stage').id)
        return res
