from odoo.exceptions import AccessDenied, AccessError


class AgentAuthenticationError(AccessDenied):
    """Authentication denial carrying a stable code for API clients."""

    def __init__(self, message, code):
        super().__init__(message)
        self.context = {"usl_code": code}


class AgentPolicyAccessError(AccessError):
    """Access denial carrying a stable code for API clients."""

    def __init__(self, message, code):
        super().__init__(message)
        self.context = {"usl_code": code}
