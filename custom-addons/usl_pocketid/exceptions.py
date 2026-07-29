from odoo.exceptions import AccessDenied


class PocketIDAccessDenied(AccessDenied):
    """An expected Pocket ID denial carrying only a safe reason code."""

    def __init__(self, reason="denied"):
        super().__init__("Pocket ID access denied")
        self.reason = reason

