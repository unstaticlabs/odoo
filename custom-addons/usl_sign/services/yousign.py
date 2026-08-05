import json
import re

import requests


SANITIZED_ERRORS = {
    400: "The provider rejected the signature request configuration.",
    401: "The signature provider credentials are invalid.",
    403: "The signature provider account does not permit this operation.",
    404: "The signature provider record could not be found.",
    409: "The signature provider reported a conflicting operation.",
    415: "The signature provider rejected the document format.",
    429: "The signature provider rate limit was reached.",
}


class YousignClient:
    """Small deterministic API v3 client; orchestration remains in Odoo models."""

    def __init__(self, configuration, *, session=None, error_class=RuntimeError):
        self.base_url = configuration["base_url"].rstrip("/")
        self.workspace_id = configuration.get("workspace_id")
        self.session = session or requests.Session()
        self.error_class = error_class
        self.headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {configuration['api_key']}",
            "User-Agent": "USL-Odoo-Sign/1.0",
        }

    def _request(
        self,
        method,
        path,
        *,
        expected=(200,),
        json=None,
        params=None,
        files=None,
        data=None,
        accept=None,
    ):
        headers = dict(self.headers)
        if accept:
            headers["Accept"] = accept
        try:
            response = self.session.request(
                method,
                f"{self.base_url}{path}",
                headers=headers,
                json=json,
                params=params,
                files=files,
                data=data,
                timeout=(10, 60),
            )
        except requests.RequestException as error:
            raise self.error_class(
                "The signature provider could not be reached. Reconcile before retrying.",
                retryable=True,
                uncertain=method.upper() not in {"GET", "HEAD"},
            ) from error
        if response.status_code not in expected:
            retryable = response.status_code == 429 or response.status_code >= 500
            message = SANITIZED_ERRORS.get(
                response.status_code,
                "The signature provider returned an unexpected response.",
            )
            raise self.error_class(
                message,
                retryable=retryable,
                uncertain=retryable and method.upper() not in {"GET", "HEAD"},
                status_code=response.status_code,
            )
        if response.status_code == 204 or not response.content:
            return None
        if accept and accept != "application/json":
            return response.content
        try:
            return response.json()
        except ValueError as error:
            raise self.error_class(
                "The signature provider returned an unreadable response."
            ) from error

    def create_request(self, payload):
        body = {
            "name": payload["name"][:255],
            "delivery_mode": "none",
            "timezone": payload.get("timezone", "Europe/Paris"),
            "audit_trail_locale": payload.get("audit_trail_locale", "fr"),
            "external_id": payload["external_id"][:255],
            "ordered_signers": bool(payload.get("ordered_signers")),
        }
        if payload.get("expiration_date"):
            body["expiration_date"] = payload["expiration_date"]
        if payload.get("reminder_settings"):
            body["reminder_settings"] = payload["reminder_settings"]
        if self.workspace_id:
            body["workspace_id"] = self.workspace_id
        return self._request(
            "POST", "/signature_requests", expected=(201,), json=body
        )

    def recover_request(self, external_id):
        payload = self._request(
            "GET",
            "/signature_requests",
            params={"external_id[eq]": external_id, "limit": 2},
        )
        rows = payload.get("data", payload) if isinstance(payload, dict) else payload
        rows = rows or []
        if len(rows) > 1:
            raise self.error_class(
                "The provider returned more than one transaction for the operation key."
            )
        return rows[0] if rows else None

    def upload_document(self, request_id, filename, content, initials=None):
        safe_name = re.sub(r"[\\/\"]", "-", filename).strip()[:128] or "document.pdf"
        data = {"nature": "signable_document", "name": safe_name}
        if initials:
            data["initials"] = json.dumps(initials, separators=(",", ":"))
        return self._request(
            "POST",
            f"/signature_requests/{request_id}/documents",
            expected=(201,),
            files={"file": (safe_name, content, "application/pdf")},
            data=data,
        )

    def add_signer(self, request_id, payload):
        return self._request(
            "POST",
            f"/signature_requests/{request_id}/signers",
            expected=(201,),
            json=payload,
        )

    def add_field(self, request_id, document_id, payload):
        return self._request(
            "POST",
            f"/signature_requests/{request_id}/documents/{document_id}/fields",
            expected=(201,),
            json=payload,
        )

    def list_fields(self, request_id, document_id):
        payload = self._request(
            "GET",
            f"/signature_requests/{request_id}/documents/{document_id}/fields",
            params={"limit": 100},
        )
        return payload.get("data", payload) if isinstance(payload, dict) else payload

    def activate(self, request_id):
        return self._request(
            "POST", f"/signature_requests/{request_id}/activate", expected=(201,)
        )

    def get_request(self, request_id):
        return self._request("GET", f"/signature_requests/{request_id}")

    def cancel(self, request_id):
        snapshot = self.get_request(request_id)
        if snapshot.get("status") == "draft":
            return self._request(
                "DELETE", f"/signature_requests/{request_id}", expected=(204,)
            )
        return self._request(
            "POST",
            f"/signature_requests/{request_id}/cancel",
            expected=(201,),
            json={"reason": "other", "custom_note": "Cancelled from Odoo"},
        )

    def download_document(self, request_id, document_id):
        return self._request(
            "GET",
            f"/signature_requests/{request_id}/documents/{document_id}/download",
            accept="application/pdf",
        )

    def download_audit_trail(self, request_id, signer_id):
        payload = self._request(
            "GET",
            f"/signature_requests/{request_id}/signers/{signer_id}/audit_trails",
        )
        return json.dumps(
            payload,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode()

    def healthcheck(self):
        return self._request("GET", "/workspaces/default")
