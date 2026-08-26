TRUST_LEVELS = [
    ("standard", "Standard electronic signature with reinforced evidence."),
    (
        "strong_personal",
        "Strong personal signature — designed for advanced-signature requirements.",
    ),
    ("qualified_external", "Qualified external signature."),
]

DOCUMENT_CATEGORIES = [
    ("routine_agreement", "Routine agreement"),
    ("employment", "Employment document"),
    ("intellectual_property", "Intellectual property"),
    ("commercial", "Commercial agreement"),
    ("finance_guarantee", "Financing or guarantee"),
    ("mandate", "Mandate"),
    ("other", "Other"),
]

# An object-identity capability cannot be reproduced by JSON-RPC context input.
# Every protected Sign mutation must carry this exact in-process sentinel.
INTERNAL_OPERATION = object()

# The public result page carries only a short-lived, non-sensitive summary in
# the current browser session. It never puts request or signer data in a URL.
SIGN_RESULT_SESSION_KEY = "usl_sign_last_result"

REQUEST_STATES = [
    ("draft", "Draft"),
    ("ready", "Ready"),
    ("sent", "Sent"),
    ("viewed", "Viewed"),
    ("partial", "Partially signed"),
    ("waiting_enrollment", "Waiting for enrolment"),
    ("waiting_external", "Waiting for external signature"),
    ("signed_to_import", "Signed document to import"),
    ("validating", "Validation in progress"),
    ("completed", "Completed"),
    ("evidence_incomplete", "Evidence incomplete"),
    ("validation_failed", "Validation failed"),
    ("declined", "Declined"),
    ("expired", "Expired"),
    ("cancelled", "Cancelled"),
    ("action_required", "Action required"),
]

SIGNER_STATES = [
    ("draft", "Draft"),
    ("notified", "Notified"),
    ("viewed", "Viewed"),
    ("authorized", "Authorized"),
    ("signed", "Signed"),
    ("declined", "Declined"),
    ("expired", "Expired"),
    ("cancelled", "Cancelled"),
]

AUTHENTICATION_METHODS = [
    ("secure_link", "Secure invitation link"),
    ("email_otp", "Secure link plus email verification code"),
    ("pocket_id", "Pocket ID"),
    ("portal", "Odoo portal account"),
    ("pocket_id_passkey", "Fresh Pocket ID passkey"),
    ("external_provider", "External qualified-signature provider"),
]

ACTIVE_REQUEST_STATES = {
    "ready",
    "sent",
    "viewed",
    "partial",
    "waiting_enrollment",
    "waiting_external",
    "signed_to_import",
    "validating",
    "evidence_incomplete",
    "action_required",
}

EXPIRABLE_REQUEST_STATES = {
    "sent",
    "viewed",
    "partial",
    "waiting_enrollment",
    "waiting_external",
}

CANCELLABLE_REQUEST_STATES = {
    "draft",
    "ready",
    "sent",
    "viewed",
    "partial",
    "waiting_enrollment",
    "waiting_external",
    "signed_to_import",
    "action_required",
}

TERMINAL_REQUEST_STATES = {
    "completed",
    "validation_failed",
    "declined",
    "expired",
    "cancelled",
}

MUTABLE_REQUEST_STATES = {"draft", "ready"}
