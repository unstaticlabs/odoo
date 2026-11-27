from odoo import http
from odoo.addons.auth_signup.controllers.main import AuthBaseSetup
from odoo.http import request


class AgentSettings(AuthBaseSetup):
    @http.route()
    def base_setup_data(self, **kw):
        data = super().base_setup_data(**kw)
        # An agent never signs in, so it would sit among the pending invitations forever.
        pending = request.env["res.users"].search([
            ("share", "=", False), ("log_ids", "=", False),
            ("usl_identity_classification", "!=", "agent"),
        ], order="id desc")
        shown = pending[:10]
        data.update(
            pending_count=len(pending),
            pending_users=[(user.id, user.login) for user in shown],
            action_pending_users=shown._action_show(),
        )
        return data
