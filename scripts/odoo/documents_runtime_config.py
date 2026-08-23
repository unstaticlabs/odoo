"""Apply the environment-specific Paperless runtime connection to Odoo."""

# Odoo shell injects ``env`` and executes this file directly.
# ruff: noqa: EM101, F821, I001

import os
import urllib.parse


internal_url = os.environ.get("PAPERLESS_RUNTIME_INTERNAL_URL", "").rstrip("/")
public_url = os.environ.get("PAPERLESS_RUNTIME_PUBLIC_URL", "").rstrip("/")
token = os.environ.get("PAPERLESS_RUNTIME_TOKEN", "").strip()
service_user_id = os.environ.get("PAPERLESS_RUNTIME_SERVICE_USER_ID", "").strip()
parsed = urllib.parse.urlsplit(public_url)
if parsed.scheme not in {"http", "https"} or not parsed.netloc:
    raise RuntimeError("PAPERLESS_RUNTIME_PUBLIC_URL must be an absolute HTTP URL")

params = env["ir.config_parameter"].sudo()
params.set_str("usl_documents.paperless_public_url", public_url)
if any((internal_url, token, service_user_id)):
    internal = urllib.parse.urlsplit(internal_url)
    if internal.scheme not in {"http", "https"} or not internal.netloc:
        raise RuntimeError(
            "PAPERLESS_RUNTIME_INTERNAL_URL must be an absolute HTTP URL",
        )
    if not token:
        raise RuntimeError("PAPERLESS_RUNTIME_TOKEN is required")
    if not service_user_id.isdigit() or int(service_user_id) < 1:
        raise RuntimeError(
            "PAPERLESS_RUNTIME_SERVICE_USER_ID must be a positive integer",
        )
    params.set_str("usl_documents.paperless_url", internal_url)
    params.set_str("usl_documents.paperless_token", token)
    params.set_int("usl_documents.paperless_service_user_id", int(service_user_id))
env.cr.commit()
