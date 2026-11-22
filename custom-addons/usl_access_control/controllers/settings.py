from odoo import http
from odoo.addons.base_setup.controllers.main import BaseSetup
from odoo.http import request


class AgentSettings(BaseSetup):
    @http.route()
    def base_setup_data(self, **kw):
        data = super().base_setup_data(**kw)
        # The invitation widget also uses the original logins to detect duplicates.
        pending = request.env["res.users"].search([
            ("id", "in", [uid for uid, _login in data["pending_users"]]),
            ("usl_identity_classification", "=", "agent"),
        ])
        data["usl_pending_agent_names"] = {
            user.id: user.name for user in pending
        }
        return data
