from odoo.exceptions import AccessError


class AgentPolicyAccessError(AccessError):
    """Access denial carrying a stable code for API clients."""

    def __init__(self, message, code):
        super().__init__(message)
        self.context = {"usl_code": code}
