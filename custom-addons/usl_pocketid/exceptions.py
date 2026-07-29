from odoo.exceptions import AccessDenied


class PocketIDReason:
    """Stable, non-sensitive denial codes safe for redirects and audit events."""

    CONFIGURATION = "configuration"
    DENIED = "denied"
    GROUP_REQUIRED = "group_required"
    IDENTITY_CONFLICT = "identity_conflict"
    IDENTITY_DISABLED = "identity_disabled"
    IDENTITY_UNLINKED = "identity_unlinked"
    PROVIDER_DENIED = "provider_denied"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    TOKEN_INVALID = "token_invalid"
    USER_DISABLED = "user_disabled"


class PocketIDAccessDenied(AccessDenied):
    """An expected Pocket ID denial carrying only a safe reason code."""

    def __init__(self, reason=PocketIDReason.DENIED):
        super().__init__("Pocket ID access denied")
        self.reason = reason
