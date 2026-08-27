import hashlib
import json
import os
import ssl
import urllib.error
import urllib.request
import uuid

from odoo import api, models, _
from odoo.exceptions import UserError

MAX_PDF_BYTES = 20 * 1024 * 1024


class UslDocumentRenderer(models.AbstractModel):
    _name = "usl.document.renderer"
    _description = "USL Document Renderer Client"

    @api.model
    def _parameter(self, name, *, required=False):
        value = self.env["ir.config_parameter"].sudo().get_str(name)
        if required and not value:
            raise UserError(_("Renderer setting %s is not configured.", name))
        return value

    @api.model
    def _base_url(self):
        url = self._parameter("usl_document_templates.renderer_url", required=True).rstrip("/")
        if not url.startswith("https://"):
            if os.environ.get("USL_DOCUMENT_RENDERER_ALLOW_PLAINTEXT") != "1":
                raise UserError(_("The document renderer URL must use HTTPS."))
        return url

    @api.model
    def _ssl_context(self):
        if self._base_url().startswith("http://"):
            return None
        ca_path = self._parameter("usl_document_templates.renderer_ca_path", required=True)
        certificate_path = self._parameter(
            "usl_document_templates.renderer_certificate_path", required=True
        )
        private_key_path = self._parameter(
            "usl_document_templates.renderer_private_key_path", required=True
        )
        try:
            context = ssl.create_default_context(cafile=ca_path)
            context.minimum_version = ssl.TLSVersion.TLSv1_3
            context.load_cert_chain(certificate_path, private_key_path)
        except (OSError, ssl.SSLError) as error:
            raise UserError(_("The document renderer mTLS credentials are invalid.")) from error
        return context

    @api.model
    def _request(self, path, *, payload=None, expect_pdf=False):
        url = self._base_url() + path
        headers = {
            "Accept": "application/pdf" if expect_pdf else "application/json",
            "X-Request-ID": str(uuid.uuid4()),
        }
        body = None
        if payload is not None:
            body = json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(
            url,
            data=body,
            headers=headers,
            method="POST" if body is not None else "GET",
        )
        timeout = int(self._parameter("usl_document_templates.renderer_timeout") or 35)
        try:
            with urllib.request.urlopen(
                request,
                context=self._ssl_context(),
                timeout=timeout,
            ) as response:
                response_headers = response.headers
                content_length = int(
                    response_headers.get("Content-Length", "0") or 0
                )
                if expect_pdf and content_length > MAX_PDF_BYTES:
                    raise UserError(
                        _("The renderer response exceeds the allowed PDF size.")
                    )
                content = response.read(
                    MAX_PDF_BYTES + 1 if expect_pdf else 1024 * 1024
                )
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as error:
            raise UserError(_("The document renderer is unavailable. Try again or open Settings.")) from error
        if expect_pdf:
            if len(content) > MAX_PDF_BYTES or not content.startswith(b"%PDF-"):
                raise UserError(_("The document renderer returned an invalid PDF."))
            return content, response_headers
        try:
            return json.loads(content)
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise UserError(_("The document renderer returned an invalid response.")) from error

    @api.model
    def _expected_revision(self):
        return self._parameter(
            "usl_document_templates.renderer_expected_revision",
            required=True,
        )

    @api.model
    @api.private
    def health(self):
        health = self._request("/health")
        expected = self._expected_revision()
        if health.get("template_revision") != expected:
            raise UserError(
                _(
                    "Renderer revision mismatch: expected %(expected)s, received %(received)s.",
                    expected=expected,
                    received=health.get("template_revision") or _("none"),
                )
            )
        if health.get("status") != "ok":
            raise UserError(_("The document renderer reported an unhealthy state."))
        return health

    @api.model
    @api.private
    def render(self, template, company, document, locale, assets=None):
        expected_revision = self._expected_revision()
        envelope = {
            "request_id": str(uuid.uuid4()),
            "template_key": template.key,
            "schema_version": template.schema_version,
            "locale": locale,
            "output_profile": template.output_profile,
            "company": company,
            "document": document,
            "assets": assets or [],
        }
        canonical = json.dumps(
            envelope,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        expected_digest = hashlib.sha256(canonical).hexdigest()
        pdf, headers = self._request("/v1/render", payload=envelope, expect_pdf=True)
        revision = headers.get("X-USL-Template-Revision")
        digest = headers.get("X-USL-Payload-SHA256")
        if revision != expected_revision:
            raise UserError(_("The renderer used an unexpected template revision."))
        if digest != expected_digest:
            raise UserError(_("The renderer payload digest did not match the request."))
        return {
            "pdf": pdf,
            "template_revision": revision,
            "payload_sha256": digest,
            "renderer_version": headers.get("X-USL-Engine-Version"),
        }
