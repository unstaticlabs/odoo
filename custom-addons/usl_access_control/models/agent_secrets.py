import re


AGENT_HIDDEN_API_MODELS = frozenset(
    {
        "auth.passkey.key",
        "auth.passkey.key.create",
        "certificate.key",
        "ir.config_parameter",
        "mail.ice.server",
        "mail.push.device",
        "payment.token",
        "res.users.apikeys",
        "res.users.apikeys.description",
        "res.users.apikeys.show",
        "usl.agent.credential",
        "usl.agent.key.wizard",
    },
)

_MODEL_SECRET_FIELDS = {
    "account_edi_proxy_client.user": frozenset({"private_key_id", "refresh_token"}),
    "certificate.certificate": frozenset(
        {
            "content",
            "pem_certificate",
            "pkcs12_password",
            "private_key_id",
            "public_key_id",
        },
    ),
    "fetchmail.server": frozenset(
        {
            "google_gmail_access_token",
            "google_gmail_refresh_token",
            "microsoft_outlook_access_token",
            "microsoft_outlook_refresh_token",
            "password",
            "user",
        },
    ),
    "ir.mail_server": frozenset(
        {
            "google_gmail_access_token",
            "google_gmail_refresh_token",
            "microsoft_outlook_access_token",
            "microsoft_outlook_refresh_token",
            "smtp_pass",
            "smtp_user",
        },
    ),
    "res.config.settings": frozenset(
        {
            "usl_document_renderer_private_key_path",
        },
    ),
}

_EXACT_SECRET_FIELDS = frozenset(
    {
        "api_key",
        "api_secret",
        "client_secret",
        "credential",
        "credentials",
        "imap_password",
        "imap_pass",
        "imap_user",
        "key",
        "oauth_secret",
        "password",
        "passwd",
        "private_key",
        "refresh_token",
        "secret",
        "smtp_password",
        "smtp_pass",
        "smtp_user",
        "token",
        "webhook_secret",
    },
)
_SECRET_SUFFIX = re.compile(
    r"(?:^|_)(?:api_key|api_secret|client_secret|credential|credentials|pass|password|passwd|private_key|refresh_token|secret|token)$",
)
_SECRET_MATERIAL = re.compile(
    r"(?:^|_)(?:access_token|api_key|api_secret|auth_token|bearer_token|client_secret|"
    r"id_token|invitation_token|otp_exchange_token|password|passwd|private_key|"
    r"refresh_token|secret|session_token|verification_token|webhook_secret)"
    r"(?:$|_(?:digest|hash|sha256)$)",
)


def is_agent_secret_field(field_name, *, model_name=None):
    normalized = str(field_name or "").strip().lower().replace("-", "_")
    path_parts = tuple(
        part
        for part in re.split(r"[./]", normalized)
        if part
    )
    return bool(
        normalized in _MODEL_SECRET_FIELDS.get(model_name, ())
        or any(
            part in _EXACT_SECRET_FIELDS
            or _SECRET_SUFFIX.search(part)
            or _SECRET_MATERIAL.search(part)
            for part in path_parts
        )
    )


def sanitize_agent_payload(value, *, model_name=None):
    if isinstance(value, dict):
        return {
            key: sanitize_agent_payload(item, model_name=model_name)
            for key, item in value.items()
            if not is_agent_secret_field(key, model_name=model_name)
        }
    if isinstance(value, list):
        return [sanitize_agent_payload(item, model_name=model_name) for item in value]
    if isinstance(value, tuple):
        return tuple(
            sanitize_agent_payload(item, model_name=model_name)
            for item in value
        )
    return value
