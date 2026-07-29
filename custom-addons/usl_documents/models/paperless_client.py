import json
import logging
import urllib.error
import urllib.parse
import urllib.request
import uuid

from odoo import _
from odoo.exceptions import AccessError, UserError

_logger = logging.getLogger(__name__)


class PaperlessError(UserError):
    """A safe, actionable Paperless integration error."""


class PaperlessUnavailable(PaperlessError):
    pass


class PaperlessAuthenticationError(PaperlessError):
    pass


class PaperlessCompatibilityError(PaperlessError):
    pass


class PaperlessClient:
    API_VERSION = "10"
    SUPPORTED_SERVER_MAJOR = 3

    def __init__(self, env):
        self.env = env
        params = env["ir.config_parameter"].sudo()
        self.base_url = params.get_str("usl_documents.paperless_url", "").rstrip("/")
        self.token = params.get_str("usl_documents.paperless_token", "")
        self.public_url = params.get_str(
            "usl_documents.paperless_public_url", self.base_url
        ).rstrip("/")
        self.timeout = params.get_int("usl_documents.paperless_timeout", 20)
        self.owner_user_id = params.get_int(
            "usl_documents.paperless_service_user_id", 0
        )

    @property
    def configured(self):
        return bool(self.base_url and self.token)

    def _headers(self, *, json_body=False):
        headers = {
            "Accept": f"application/json; version={self.API_VERSION}",
            "Authorization": f"Token {self.token}",
            "User-Agent": "USL-Odoo-Documents/1.0",
        }
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    def _request(
        self,
        method,
        path,
        *,
        query=None,
        body=None,
        headers=None,
        raw=False,
    ):
        if not self.configured:
            raise PaperlessUnavailable(
                _("Paperless is not configured. Ask a Documents administrator.")
            )
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query, doseq=True)}"
        request_headers = self._headers(json_body=body is not None)
        request_headers.update(headers or {})
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            url, data=data, headers=request_headers, method=method
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = response.read()
                response_headers = dict(response.headers.items())
                if raw:
                    return payload, response_headers
                return (
                    json.loads(payload.decode()) if payload else {},
                    response_headers,
                )
        except urllib.error.HTTPError as error:
            payload = error.read().decode(errors="replace")[:1000]
            if error.code in (401, 403):
                raise PaperlessAuthenticationError(
                    _("Paperless rejected the integration identity.")
                ) from error
            if error.code == 406:
                raise PaperlessCompatibilityError(
                    _("Paperless does not support required API version %s.")
                    % self.API_VERSION
                ) from error
            raise PaperlessError(
                _("Paperless request failed (%(status)s): %(detail)s")
                % {"status": error.code, "detail": payload}
            ) from error
        except (TimeoutError, urllib.error.URLError, OSError) as error:
            raise PaperlessUnavailable(
                _("Paperless is unavailable. Odoo remains usable; retry later.")
            ) from error

    def compatibility(self):
        payload, headers = self._request("GET", "/api/documents/", query={"page_size": 1})
        normalized_headers = {key.lower(): value for key, value in headers.items()}
        api_version = normalized_headers.get("x-api-version")
        server_version = normalized_headers.get("x-version", "")
        if api_version != self.API_VERSION:
            raise PaperlessCompatibilityError(
                _("Expected Paperless API %(expected)s, received %(actual)s.")
                % {"expected": self.API_VERSION, "actual": api_version or _("unknown")}
            )
        try:
            major = int(server_version.split(".", 1)[0])
        except (TypeError, ValueError):
            major = None
        if major != self.SUPPORTED_SERVER_MAJOR:
            raise PaperlessCompatibilityError(
                _("Paperless server %(version)s is outside the qualified 3.x series.")
                % {"version": server_version or _("unknown")}
            )
        return {
            "ok": True,
            "api_version": api_version,
            "server_version": server_version,
            "document_count": payload.get("count", 0),
        }

    def list_documents(self, *, page=1, page_size=100, modified_after=None):
        query = {"page": page, "page_size": page_size, "ordering": "id"}
        if modified_after:
            query["modified__gt"] = modified_after
        return self._request("GET", "/api/documents/", query=query)[0]

    def search(self, text, *, page=1, page_size=50, filters=None):
        query = {"page": page, "page_size": page_size}
        if text:
            query["text"] = text
        query.update(filters or {})
        return self._request("GET", "/api/documents/", query=query)[0]

    def get_document(self, document_id, *, version_id=None):
        query = {"version": version_id} if version_id else None
        return self._request(
            "GET", f"/api/documents/{int(document_id)}/", query=query
        )[0]

    def get_versions(self, document_id):
        payload = self.get_document(document_id)
        return payload.get("versions") or []

    def download(self, document_id, *, version_id=None, original=False):
        query = {}
        if version_id:
            query["version"] = version_id
        if original:
            query["original"] = "true"
        return self._request(
            "GET",
            f"/api/documents/{int(document_id)}/download/",
            query=query or None,
            raw=True,
        )

    def preview(self, document_id, *, version_id=None):
        query = {"version": version_id} if version_id else None
        return self._request(
            "GET",
            f"/api/documents/{int(document_id)}/preview/",
            query=query,
            raw=True,
        )

    def thumbnail(self, document_id):
        return self._request(
            "GET", f"/api/documents/{int(document_id)}/thumb/", raw=True
        )

    def upload_multipart(self, content, filename, content_type, *, title=None):
        if not self.configured:
            raise PaperlessUnavailable(
                _("Paperless is not configured. Ask a Documents administrator.")
            )
        boundary = f"----usl-{uuid.uuid4().hex}"
        chunks = []
        if title:
            chunks.append(
                (
                    f"--{boundary}\r\n"
                    'Content-Disposition: form-data; name="title"\r\n\r\n'
                    f"{title}\r\n"
                ).encode()
            )
        safe_filename = filename.replace('"', "")
        chunks.extend([
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="document"; filename="{safe_filename}"\r\n'
                f"Content-Type: {content_type or 'application/octet-stream'}\r\n\r\n"
            ).encode(),
            content,
            f"\r\n--{boundary}--\r\n".encode(),
        ])
        request = urllib.request.Request(
            f"{self.base_url}/api/documents/post_document/",
            data=b"".join(chunks),
            headers={
                **self._headers(),
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                task_id = json.loads(response.read().decode())
                return str(task_id)
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                raise PaperlessAuthenticationError(
                    _("Paperless rejected the integration identity.")
                ) from error
            raise PaperlessError(
                _("Paperless rejected the upload (%s).") % error.code
            ) from error
        except (TimeoutError, urllib.error.URLError, OSError) as error:
            raise PaperlessUnavailable(
                _("Paperless is unavailable. The upload was not archived.")
            ) from error

    def task(self, task_id):
        payload = self._request(
            "GET", "/api/tasks/", query={"task_id": task_id}
        )[0]
        results = payload.get("results", payload if isinstance(payload, list) else [])
        return results[0] if results else None

    def set_document_permissions(self, document_id, *, view_users, change_users):
        if not self.owner_user_id:
            raise PaperlessCompatibilityError(
                _(
                    "The dedicated Paperless service identity ID is not configured; "
                    "permission synchronization is blocked."
                )
            )
        permissions = {
            "view": {"users": sorted(set(view_users)), "groups": []},
            "change": {"users": sorted(set(change_users)), "groups": []},
        }
        return self._request(
            "POST",
            "/api/documents/bulk_edit/",
            body={
                "documents": [int(document_id)],
                "method": "set_permissions",
                "parameters": {
                    "set_permissions": permissions,
                    "owner": self.owner_user_id,
                    "merge": False,
                },
            },
        )[0]

    def paperless_url(self, document_id=None):
        if not self.public_url:
            raise UserError(_("The Paperless public URL is not configured."))
        if document_id:
            return f"{self.public_url}/documents/{int(document_id)}/details"
        return self.public_url
