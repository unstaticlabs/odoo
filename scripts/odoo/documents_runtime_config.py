"""Apply non-secret Documents runtime endpoints to the selected Odoo database."""

import os
import urllib.parse


public_url = os.environ.get("PAPERLESS_RUNTIME_PUBLIC_URL", "").rstrip("/")
parsed = urllib.parse.urlsplit(public_url)
if parsed.scheme not in {"http", "https"} or not parsed.netloc:
    raise RuntimeError("PAPERLESS_RUNTIME_PUBLIC_URL must be an absolute HTTP URL")

env["ir.config_parameter"].sudo().set_str(  # noqa: F821
    "usl_documents.paperless_public_url",
    public_url,
)
env.cr.commit()  # noqa: F821
