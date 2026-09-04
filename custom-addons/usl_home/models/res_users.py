from odoo import api, models


class ResUsers(models.Model):
    _inherit = "res.users"

    @api.model_create_multi
    def create(self, vals_list):
        users = super().create(vals_list)
        home_action = self.env.ref("usl_home.action_usl_home", raise_if_not_found=False)
        if not home_action:
            return users
        for user, vals in zip(users, vals_list):
            is_agent = (
                vals.get("usl_identity_classification") == "agent"
                if "usl_identity_classification" in user._fields
                else False
            )
            if "action_id" not in vals and user._is_internal() and not is_agent:
                # ``action_id`` targets the polymorphic ir.actions.actions table.
                # Writing the identifier avoids assigning an ir.actions.client
                # recordset to the base-model Many2one descriptor.
                user.sudo().write({"action_id": home_action.id})
        return users
