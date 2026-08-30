import copy
import hashlib
import json
import logging
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from odoo import _
from odoo.exceptions import UserError

_logger = logging.getLogger(__name__)


class PaperlessError(UserError):
    """A safe, actionable Paperless integration error."""


class PaperlessUnavailable(PaperlessError):
    pass


class PaperlessAuthenticationError(PaperlessError):
    pass


class PaperlessCompatibilityError(PaperlessError):
    pass


class PaperlessNotFound(PaperlessError):
    pass


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file_pointer, code, message, headers, url):
        return None


class PaperlessBinaryStream:
    """One open Paperless response whose body is consumed incrementally."""

    def __init__(self, response):
        self._response = response
        status = getattr(response, "status", None)
        self.status = int(status if status is not None else response.getcode())
        self.headers = dict(response.headers.items())
        try:
            self.expected_length = int(self.headers.get("Content-Length", ""))
        except (TypeError, ValueError):
            self.expected_length = None
        if self.expected_length is not None and self.expected_length < 0:
            self.expected_length = None

    def iter_chunks(self, chunk_size=64 * 1024):
        received = 0
        try:
            while chunk := self._response.read(chunk_size):
                received += len(chunk)
                yield chunk
            if self.expected_length is not None and received != self.expected_length:
                raise PaperlessUnavailable(
                    "Paperless ended the document stream before completion.",
                )
        finally:
            self.close()

    def close(self):
        if self._response is not None:
            self._response.close()
            self._response = None


class PaperlessClient:
    API_VERSION = "10"
    SUPPORTED_SERVER_MAJOR = 3
    FAIL_CLOSED_WORKFLOW_NAME = "USL Odoo fail-closed ingestion"
    _SCOPED_SEARCH_CACHE_TTL = 5.0
    _SCOPED_SEARCH_CACHE_LIMIT = 128
    _scoped_search_cache = {}
    _scoped_search_cache_lock = threading.Lock()
    _SEMANTIC_SEARCH_CACHE_TTL = 30.0
    _SEMANTIC_SEARCH_CACHE_LIMIT = 64
    _semantic_search_cache = {}
    _semantic_search_cache_lock = threading.Lock()

    def __init__(self, env):
        self.env = env
        params = env["ir.config_parameter"].sudo()
        self.base_url = params.get_str("usl_documents.paperless_url", "").rstrip("/")
        self.token = params.get_str("usl_documents.paperless_token", "")
        self.public_url = params.get_str(
            "usl_documents.paperless_public_url", self.base_url,
        ).rstrip("/")
        self.timeout = params.get_int("usl_documents.paperless_timeout", 20)
        self.stream_timeout = params.get_int(
            "usl_documents.paperless_stream_timeout", 60,
        )
        self.owner_user_id = params.get_int(
            "usl_documents.paperless_service_user_id", 0,
        )
        self._opener = urllib.request.build_opener(_NoRedirectHandler())

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

    @staticmethod
    def _decode_json(payload):
        try:
            return json.loads(payload.decode())
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            message = "Paperless returned an invalid API response."
            raise PaperlessCompatibilityError(message) from error

    @staticmethod
    def _multipart_filename(filename):
        """Keep an untrusted filename inside one multipart header value."""
        return (
            str(filename or "document")
            .replace("\r", "_")
            .replace("\n", "_")
            .replace('"', "'")
        )

    @staticmethod
    def _multipart_content_type(content_type):
        value = str(content_type or "")
        if re.fullmatch(r"[\w!#$&^_.+-]+/[\w!#$&^_.+-]+", value):
            return value
        return "application/octet-stream"

    @staticmethod
    def _multipart_text(value):
        """Keep scalar metadata inside one multipart form value."""
        return str(value or "").replace("\r", " ").replace("\n", " ")

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
                _("Paperless is not configured. Ask a Documents administrator."),
            )
        url = f"{self.base_url}{path}"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query, doseq=True)}"
        request_headers = self._headers(json_body=body is not None)
        request_headers.update(headers or {})
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(
            url, data=data, headers=request_headers, method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = response.read()
                response_headers = dict(response.headers.items())
                if raw:
                    return payload, response_headers
                return (self._decode_json(payload) if payload else {}, response_headers)
        except urllib.error.HTTPError as error:
            payload = error.read().decode(errors="replace")[:1000]
            if error.code in (401, 403):
                raise PaperlessAuthenticationError(
                    _("Paperless rejected the integration identity."),
                ) from error
            if error.code == 404:
                raise PaperlessNotFound(
                    _("The requested Paperless document or version no longer exists."),
                ) from error
            if error.code == 406:
                raise PaperlessCompatibilityError(
                    _("Paperless does not support required API version %s.")
                    % self.API_VERSION,
                ) from error
            if error.code == 503:
                raise PaperlessUnavailable(
                    _("Paperless search is temporarily unavailable."),
                ) from error
            raise PaperlessError(
                _("Paperless request failed (%(status)s): %(detail)s")
                % {"status": error.code, "detail": payload},
            ) from error
        except (TimeoutError, urllib.error.URLError, OSError) as error:
            raise PaperlessUnavailable(
                _("Paperless is unavailable. Odoo remains usable; retry later."),
            ) from error

    def compatibility(self):
        payload, headers = self._request("GET", "/api/documents/", query={"page_size": 1})
        normalized_headers = {key.lower(): value for key, value in headers.items()}
        api_version = normalized_headers.get("x-api-version")
        server_version = normalized_headers.get("x-version", "")
        if api_version != self.API_VERSION:
            raise PaperlessCompatibilityError(
                _("Expected Paperless API %(expected)s, received %(actual)s.")
                % {"expected": self.API_VERSION, "actual": api_version or _("unknown")},
            )
        try:
            major = int(server_version.split(".", 1)[0])
        except (TypeError, ValueError):
            major = None
        if major != self.SUPPORTED_SERVER_MAJOR:
            raise PaperlessCompatibilityError(
                _("Paperless server %(version)s is outside the qualified 3.x series.")
                % {"version": server_version or _("unknown")},
            )
        return {
            "ok": True,
            "api_version": api_version,
            "server_version": server_version,
            "document_count": payload.get("count", 0),
        }

    def list_documents(
        self, *, page=1, page_size=100, modified_after=None, modified_before=None,
    ):
        query = {"page": page, "page_size": page_size, "ordering": "id"}
        if modified_after:
            query["modified__gt"] = modified_after
        if modified_before:
            query["modified__lte"] = modified_before
        return self._request("GET", "/api/documents/", query=query)[0]

    def metadata_catalog(self):
        """Return the supported document metadata objects keyed by stable ID."""
        catalog = {}
        for key in ("correspondents", "document_types", "tags"):
            catalog[key] = {
                int(item["id"]): item.get("name", "")
                for item in self.list_metadata(key)
            }
        return catalog

    @staticmethod
    def _metadata_endpoint(kind):
        if kind not in {"correspondents", "document_types", "tags"}:
            raise ValueError(f"Unsupported Paperless metadata kind: {kind}")
        return kind

    def list_metadata(self, kind):
        """Return complete metadata objects through Paperless's public API."""
        endpoint = self._metadata_endpoint(kind)
        page = 1
        values = []
        while True:
            payload = self._request(
                "GET",
                f"/api/{endpoint}/",
                query={"page": page, "page_size": 100, "ordering": "name"},
            )[0]
            results = (
                payload.get("results", []) if isinstance(payload, dict) else payload
            )
            values.extend(results)
            if not isinstance(payload, dict) or not payload.get("next"):
                return values
            page += 1

    def list_custom_fields(self):
        page = 1
        values = []
        while True:
            payload = self._request(
                "GET",
                "/api/custom_fields/",
                query={"page": page, "page_size": 100, "ordering": "name"},
            )[0]
            results = (
                payload.get("results", []) if isinstance(payload, dict) else payload
            )
            values.extend(results)
            if not isinstance(payload, dict) or not payload.get("next"):
                return values
            page += 1

    def create_custom_field(self, values):
        return self._request("POST", "/api/custom_fields/", body=values)[0]

    def delete_custom_field(self, custom_field_id):
        """Delete a custom-field definition through Paperless's public API."""
        return self._request(
            "DELETE", f"/api/custom_fields/{int(custom_field_id)}/",
        )[0]

    def create_metadata(self, kind, values):
        endpoint = self._metadata_endpoint(kind)
        return self._request("POST", f"/api/{endpoint}/", body=values)[0]

    def update_metadata(self, kind, metadata_id, values):
        endpoint = self._metadata_endpoint(kind)
        return self._request(
            "PATCH", f"/api/{endpoint}/{int(metadata_id)}/", body=values,
        )[0]

    def delete_metadata(self, kind, metadata_id):
        endpoint = self._metadata_endpoint(kind)
        return self._request(
            "DELETE", f"/api/{endpoint}/{int(metadata_id)}/",
        )[0]

    def list_saved_views(self):
        """Return all Paperless saved views through the supported API."""
        page = 1
        values = []
        while True:
            payload = self._request(
                "GET",
                "/api/saved_views/",
                query={"page": page, "page_size": 100, "ordering": "name"},
            )[0]
            results = (
                payload.get("results", []) if isinstance(payload, dict) else payload
            )
            values.extend(results)
            if not isinstance(payload, dict) or not payload.get("next"):
                return values
            page += 1

    def create_saved_view(self, values):
        return self._request("POST", "/api/saved_views/", body=values)[0]

    def update_saved_view(self, saved_view_id, values):
        return self._request(
            "PATCH", f"/api/saved_views/{int(saved_view_id)}/", body=values,
        )[0]

    def delete_saved_view(self, saved_view_id):
        return self._request(
            "DELETE", f"/api/saved_views/{int(saved_view_id)}/",
        )[0]

    def list_trashed_documents(self):
        """Return all documents currently in Paperless Trash."""
        page = 1
        values = []
        while True:
            payload = self._request(
                "GET",
                "/api/trash/",
                query={"page": page, "page_size": 100, "ordering": "id"},
            )[0]
            results = (
                payload.get("results", []) if isinstance(payload, dict) else payload
            )
            values.extend(results)
            if not isinstance(payload, dict) or not payload.get("next"):
                return values
            page += 1

    def restore_trashed_documents(self, document_ids):
        return self._request(
            "POST",
            "/api/trash/",
            body={
                "documents": [int(document_id) for document_id in document_ids],
                "action": "restore",
            },
        )[0]

    def permanently_delete_trashed_documents(self, document_ids):
        """Permanently empty explicitly selected documents from Trash."""
        return self._request(
            "POST",
            "/api/trash/",
            body={
                "documents": [int(document_id) for document_id in document_ids],
                "action": "empty",
            },
        )[0]

    def ensure_document_type(self, name):
        """Return a document type by name, creating it through the public API."""
        payload = self._request(
            "GET",
            "/api/document_types/",
            query={"name__iexact": name, "page_size": 20},
        )[0]
        results = payload.get("results", payload if isinstance(payload, list) else [])
        existing = next(
            (
                item
                for item in results
                if item.get("name", "").casefold() == name.casefold()
            ),
            None,
        )
        if existing:
            return existing
        body = {"name": name}
        if self.owner_user_id:
            body["owner"] = self.owner_user_id
        return self._request("POST", "/api/document_types/", body=body)[0]

    def update_document_metadata(self, document_id, values):
        """Update Paperless-authoritative metadata through API v10."""
        return self._request(
            "PATCH", f"/api/documents/{int(document_id)}/", body=values,
        )[0]

    def search(
        self,
        text,
        *,
        page=1,
        page_size=50,
        filters=None,
        full_text=False,
    ):
        query = {"page": page, "page_size": page_size}
        if text:
            query["query" if full_text else "text"] = text
        query.update(filters or {})
        return self._request("GET", "/api/documents/", query=query)[0]

    def scoped_search(
        self,
        text,
        *,
        document_ids,
        limit=10000,
        fields="all",
        custom_field_query=None,
        include_excerpt=False,
    ):
        """Run one bounded lexical request inside an explicit Odoo scope."""
        scope = sorted({int(document_id) for document_id in document_ids})
        if not scope:
            return {"results": [], "truncated": False}
        if fields not in {"all", "content", "custom_fields"}:
            raise PaperlessError(_("Unsupported scoped search field set."))
        body = {
            "query": str(text or ""),
            "document_ids": scope,
            "fields": fields,
            "limit": min(10000, max(1, int(limit))),
            "include_excerpt": bool(include_excerpt),
        }
        if custom_field_query:
            body["custom_field_query"] = custom_field_query
        cache_material = json.dumps(
            {
                "base_url": self.base_url,
                "token": hashlib.sha256(self.token.encode()).hexdigest(),
                "body": body,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        cache_key = hashlib.sha256(cache_material).digest()
        now = time.monotonic()
        with self._scoped_search_cache_lock:
            cached = self._scoped_search_cache.get(cache_key)
            if cached and now - cached[0] <= self._SCOPED_SEARCH_CACHE_TTL:
                return copy.deepcopy(cached[1])
            self._scoped_search_cache.pop(cache_key, None)
        payload = self._request(
            "POST",
            "/api/documents/scoped_search/",
            body=body,
        )[0]
        with self._scoped_search_cache_lock:
            if len(self._scoped_search_cache) >= self._SCOPED_SEARCH_CACHE_LIMIT:
                oldest_key = min(
                    self._scoped_search_cache,
                    key=lambda key: self._scoped_search_cache[key][0],
                )
                self._scoped_search_cache.pop(oldest_key, None)
            self._scoped_search_cache[cache_key] = (now, copy.deepcopy(payload))
        return payload

    def semantic_search(self, text, *, document_ids, limit=50, facets=None):
        """Search Paperless vectors inside an explicit Odoo-authorized scope."""
        return self._semantic_search(
            query=str(text or ""),
            document_id=None,
            document_ids=document_ids,
            limit=limit,
            facets=facets,
        )

    def semantic_search_by_document(
        self,
        document_id,
        *,
        document_ids,
        limit=50,
        facets=None,
    ):
        """Find similar roots without moving source OCR across the API boundary."""
        return self._semantic_search(
            query=None,
            document_id=int(document_id),
            document_ids=document_ids,
            limit=limit,
            facets=facets,
        )

    def _semantic_search(
        self,
        *,
        query,
        document_id,
        document_ids,
        limit,
        facets,
    ):
        scope = sorted({int(document_id) for document_id in document_ids})
        if not scope:
            return {"results": [], "warnings": []}
        if document_id is not None and document_id not in scope:
            raise PaperlessError(
                _("The similar-document source is outside the authorized scope."),
            )
        allowed_facets = {
            "tag_ids",
            "correspondent_ids",
            "document_type_ids",
            "created_after",
            "created_before",
        }
        unsupported_facets = set(facets or {}) - allowed_facets
        if unsupported_facets:
            raise PaperlessError(_("Unsupported semantic search facet."))
        bounded_limit = min(50, max(1, int(limit)))
        cache_material = json.dumps(
            {
                "base_url": self.base_url,
                "token": hashlib.sha256(self.token.encode()).hexdigest(),
                "query": query,
                "document_id": document_id,
                "document_ids": scope,
                "limit": bounded_limit,
                "facets": facets or {},
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
        cache_key = hashlib.sha256(cache_material).digest()
        now = time.monotonic()
        with self._semantic_search_cache_lock:
            cached = self._semantic_search_cache.get(cache_key)
            if cached and now - cached[0] <= self._SEMANTIC_SEARCH_CACHE_TTL:
                return copy.deepcopy(cached[1])
            self._semantic_search_cache.pop(cache_key, None)
        candidates = {}
        warnings = []
        # The distribution endpoint caps one explicit service scope at 10,000
        # roots. Chunk larger authorized populations and merge comparable cosine
        # similarities; no request ever widens to an unscoped service search.
        chunk_size = 9999 if document_id is not None else 10000
        scoped_candidates = (
            [item for item in scope if item != document_id]
            if document_id is not None
            else scope
        )
        for offset in range(0, len(scoped_candidates), chunk_size):
            chunk = scoped_candidates[offset : offset + chunk_size]
            if document_id is not None:
                chunk = [document_id, *chunk]
            body = {
                "limit": bounded_limit,
                "document_ids": chunk,
            }
            if document_id is None:
                body["query"] = query
            else:
                body["document_id"] = document_id
            body.update(facets or {})
            payload = self._request(
                "POST",
                "/api/documents/semantic_search/",
                body=body,
            )[0]
            warnings.extend(payload.get("warnings") or [])
            for item in payload.get("results") or []:
                candidate_id = int(item["id"])
                existing = candidates.get(candidate_id)
                if existing is None or float(item.get("similarity") or 0) > float(
                    existing.get("similarity") or 0,
                ):
                    candidates[candidate_id] = item
        results = sorted(
            candidates.values(),
            key=lambda item: (-float(item.get("similarity") or 0), int(item["id"])),
        )[:bounded_limit]
        for rank, item in enumerate(results, start=1):
            item["rank"] = rank
        result = {"results": results, "warnings": warnings}
        with self._semantic_search_cache_lock:
            if len(self._semantic_search_cache) >= self._SEMANTIC_SEARCH_CACHE_LIMIT:
                oldest_key = min(
                    self._semantic_search_cache,
                    key=lambda key: self._semantic_search_cache[key][0],
                )
                self._semantic_search_cache.pop(oldest_key, None)
            self._semantic_search_cache[cache_key] = (
                time.monotonic(),
                copy.deepcopy(result),
            )
        return result

    def get_document(self, document_id, *, version_id=None):
        query = {"version": version_id} if version_id else None
        return self._request(
            "GET", f"/api/documents/{int(document_id)}/", query=query,
        )[0]

    def get_versions(self, document_id):
        payload = self.get_document(document_id)
        return payload.get("versions") or []

    def get_user(self, user_id):
        return self._request("GET", f"/api/users/{int(user_id)}/")[0]

    def list_users(self):
        page = 1
        values = []
        while True:
            payload = self._request(
                "GET",
                "/api/users/",
                query={"page": page, "page_size": 100, "ordering": "username"},
            )[0]
            results = (
                payload.get("results", []) if isinstance(payload, dict) else payload
            )
            values.extend(results)
            if not isinstance(payload, dict) or not payload.get("next"):
                return values
            page += 1

    def trash_document(self, document_id):
        """Move a root document to Paperless Trash without permanent deletion."""
        return self._request(
            "DELETE", f"/api/documents/{int(document_id)}/",
        )[0]

    def ensure_fail_closed_ingestion_policy(self):
        """Own every ingestion channel with the service identity until Odoo syncs.

        This uses the supported Workflow API. Ordinary Paperless users must rely
        on explicit document-object grants synchronized from Odoo, never global
        document permissions.
        """
        if not self.owner_user_id:
            raise PaperlessCompatibilityError(
                _(
                    "The dedicated Paperless service identity ID is not configured; "
                    "the fail-closed ingestion workflow cannot be installed.",
                ),
            )
        workflows = self._request(
            "GET",
            "/api/workflows/",
            query={"name__iexact": self.FAIL_CLOSED_WORKFLOW_NAME, "page_size": 20},
        )[0]
        results = workflows.get(
            "results", workflows if isinstance(workflows, list) else [],
        )
        payload = {
            "name": self.FAIL_CLOSED_WORKFLOW_NAME,
            "order": -1000,
            "enabled": True,
            "triggers": [
                {
                    "type": 1,
                    # Consume folder, API upload, mail fetch, and direct web UI.
                    "sources": [1, 2, 3, 4],
                    "filter_filename": "*",
                },
            ],
            "actions": [
                {
                    "type": 1,
                    "assign_owner": self.owner_user_id,
                    "assign_view_users": [],
                    "assign_view_groups": [],
                    "assign_change_users": [],
                    "assign_change_groups": [],
                },
            ],
        }
        existing = next(
            (
                workflow
                for workflow in results
                if workflow.get("name") == self.FAIL_CLOSED_WORKFLOW_NAME
            ),
            None,
        )
        if existing:
            created = False
            triggers = existing.get("triggers") or []
            actions = existing.get("actions") or []
            trigger = triggers[0] if len(triggers) == 1 else {}
            action = actions[0] if len(actions) == 1 else {}
            matches = (
                existing.get("name") == payload["name"]
                and existing.get("order") == payload["order"]
                and existing.get("enabled") is True
                and len(triggers) == 1
                and trigger.get("type") == 1
                and sorted(trigger.get("sources") or []) == [1, 2, 3, 4]
                and trigger.get("filter_filename") == "*"
                and len(actions) == 1
                and action.get("type") == 1
                and action.get("assign_owner") == self.owner_user_id
                and not (action.get("assign_view_users") or [])
                and not (action.get("assign_view_groups") or [])
                and not (action.get("assign_change_users") or [])
                and not (action.get("assign_change_groups") or [])
            )
            if matches:
                result = existing
                updated = False
            else:
                result = self._request(
                    "PUT", f"/api/workflows/{int(existing['id'])}/", body=payload,
                )[0]
                updated = True
        else:
            result = self._request("POST", "/api/workflows/", body=payload)[0]
            created = True
            updated = False
        return {
            "ok": True,
            "created": created,
            "updated": updated,
            "workflow_id": result.get("id"),
            "workflow_name": result.get("name", self.FAIL_CLOSED_WORKFLOW_NAME),
            "owner_user_id": self.owner_user_id,
        }

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

    @staticmethod
    def _binary_headers(*, range_header=None, if_range=None):
        headers = {}
        if range_header is not None:
            value = str(range_header).strip()
            if len(value) > 200 or not re.fullmatch(
                r"bytes=(?:\d*-\d*)(?:,\d*-\d*)*", value,
            ):
                raise ValueError("Invalid HTTP byte range.")
            headers["Range"] = value
        if if_range is not None:
            value = str(if_range).strip()
            if not value or len(value) > 200 or "\r" in value or "\n" in value:
                raise ValueError("Invalid If-Range validator.")
            headers["If-Range"] = value
        return headers

    def _open_download(
        self,
        method,
        document_id,
        *,
        version_id,
        original,
        range_header=None,
        if_range=None,
    ):
        if not self.configured:
            raise PaperlessUnavailable(
                _("Paperless is not configured. Ask a Documents administrator."),
            )
        query = {}
        if version_id:
            query["version"] = version_id
        if original:
            query["original"] = "true"
        url = f"{self.base_url}/api/documents/{int(document_id)}/download/"
        if query:
            url = f"{url}?{urllib.parse.urlencode(query)}"
        headers = self._headers()
        headers.update(
            self._binary_headers(
                range_header=range_header,
                if_range=if_range,
            ),
        )
        binary_request = urllib.request.Request(url, headers=headers, method=method)
        try:
            response = self._opener.open(
                binary_request,
                timeout=max(1, min(int(self.stream_timeout), 600)),
            )
            return PaperlessBinaryStream(response)
        except urllib.error.HTTPError as error:
            if error.code == 416:
                return PaperlessBinaryStream(error)
            error.close()
            if error.code in (301, 302, 303, 307, 308):
                raise PaperlessCompatibilityError(
                    _("Paperless attempted an unsafe binary redirect."),
                ) from error
            if error.code in (401, 403):
                raise PaperlessAuthenticationError(
                    _("Paperless rejected the integration identity."),
                ) from error
            if error.code == 404:
                raise PaperlessNotFound(
                    _("The requested Paperless document or version no longer exists."),
                ) from error
            raise PaperlessError(
                _("Paperless binary request failed (%s).") % error.code,
            ) from error
        except (TimeoutError, urllib.error.URLError, OSError) as error:
            raise PaperlessUnavailable(
                _("Paperless is unavailable. Odoo remains usable; retry later."),
            ) from error

    def probe_download(self, document_id, *, version_id, original):
        stream = self._open_download(
            "HEAD",
            document_id,
            version_id=version_id,
            original=original,
        )
        try:
            return {"status": stream.status, "headers": stream.headers}
        finally:
            stream.close()

    def open_download(
        self,
        document_id,
        *,
        version_id,
        original,
        range_header=None,
        if_range=None,
        method="GET",
    ):
        if method not in ("GET", "HEAD"):
            raise ValueError("Unsupported binary request method.")
        return self._open_download(
            method,
            document_id,
            version_id=version_id,
            original=original,
            range_header=range_header,
            if_range=if_range,
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
            "GET", f"/api/documents/{int(document_id)}/thumb/", raw=True,
        )

    def upload_multipart(
        self,
        content,
        filename,
        content_type,
        *,
        title=None,
        created=None,
        correspondent_id=None,
        document_type_id=None,
        tag_ids=None,
    ):
        if not self.configured:
            raise PaperlessUnavailable(
                _("Paperless is not configured. Ask a Documents administrator."),
            )
        boundary = f"----usl-{uuid.uuid4().hex}"
        chunks = []

        def append_field(name, value):
            chunks.append(
                (
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
                    f"{self._multipart_text(value)}\r\n"
                ).encode(),
            )

        if title:
            append_field("title", title)
        if created:
            append_field("created", created)
        if correspondent_id:
            append_field("correspondent", int(correspondent_id))
        if document_type_id:
            append_field("document_type", int(document_type_id))
        for tag_id in sorted({int(tag_id) for tag_id in (tag_ids or [])}):
            append_field("tags", tag_id)
        safe_filename = self._multipart_filename(filename)
        safe_content_type = self._multipart_content_type(content_type)
        chunks.extend([
            (
                f"--{boundary}\r\n"
                f'Content-Disposition: form-data; name="document"; filename="{safe_filename}"\r\n'
                f"Content-Type: {safe_content_type}\r\n\r\n"
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
                task_id = self._decode_json(response.read())
                if isinstance(task_id, dict):
                    task_id = task_id.get("task_id") or task_id.get("id")
                if not task_id:
                    raise PaperlessCompatibilityError(
                        _("Paperless accepted the upload without returning a task ID."),
                    )
                return str(task_id)
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                raise PaperlessAuthenticationError(
                    _("Paperless rejected the integration identity."),
                ) from error
            raise PaperlessError(
                _("Paperless rejected the upload (%s).") % error.code,
            ) from error
        except (TimeoutError, urllib.error.URLError, OSError) as error:
            raise PaperlessUnavailable(
                _("Paperless is unavailable. The upload was not archived."),
            ) from error

    def update_version(
        self, document_id, content, filename, content_type, *, version_label=None,
    ):
        if not self.configured:
            raise PaperlessUnavailable(
                _("Paperless is not configured. Ask a Documents administrator."),
            )
        boundary = f"----usl-{uuid.uuid4().hex}"
        chunks = []
        if version_label:
            chunks.append(
                (
                    f"--{boundary}\r\n"
                    'Content-Disposition: form-data; name="version_label"\r\n\r\n'
                    f"{version_label}\r\n"
                ).encode(),
            )
        safe_filename = self._multipart_filename(filename)
        safe_content_type = self._multipart_content_type(content_type)
        chunks.extend(
            [
                (
                    f"--{boundary}\r\n"
                    f'Content-Disposition: form-data; name="document"; filename="{safe_filename}"\r\n'
                    f"Content-Type: {safe_content_type}\r\n\r\n"
                ).encode(),
                content,
                f"\r\n--{boundary}--\r\n".encode(),
            ],
        )
        request = urllib.request.Request(
            f"{self.base_url}/api/documents/{int(document_id)}/update_version/",
            data=b"".join(chunks),
            headers={
                **self._headers(),
                "Content-Type": f"multipart/form-data; boundary={boundary}",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = self._decode_json(response.read())
                if isinstance(payload, dict):
                    payload = payload.get("task_id") or payload.get("id")
                if not payload:
                    raise PaperlessCompatibilityError(
                        _(
                            "Paperless accepted the replacement without returning "
                            "a task ID.",
                        ),
                    )
                return str(payload)
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                raise PaperlessAuthenticationError(
                    _("Paperless rejected the integration identity."),
                ) from error
            if error.code == 404:
                raise PaperlessNotFound(
                    _("The Paperless root document no longer exists."),
                ) from error
            raise PaperlessError(
                _("Paperless rejected the replacement version (%s).") % error.code,
            ) from error
        except (TimeoutError, urllib.error.URLError, OSError) as error:
            raise PaperlessUnavailable(
                _("Paperless is unavailable. The replacement was not archived."),
            ) from error

    def task(self, task_id):
        payload = self._request(
            "GET", "/api/tasks/", query={"task_id": task_id},
        )[0]
        results = payload.get("results", payload if isinstance(payload, list) else [])
        return results[0] if results else None

    def set_document_permissions(self, document_id, *, view_users, change_users):
        return self.set_documents_permissions(
            [document_id],
            view_users=view_users,
            change_users=change_users,
        )

    def set_documents_permissions(
        self,
        document_ids,
        *,
        view_users,
        change_users,
    ):
        if not self.owner_user_id:
            raise PaperlessCompatibilityError(
                _(
                    "The dedicated Paperless service identity ID is not configured; "
                    "permission synchronization is blocked.",
                ),
            )
        document_ids = sorted({int(document_id) for document_id in document_ids})
        if not document_ids:
            return {"result": "OK"}
        permissions = {
            "view": {"users": sorted(set(view_users)), "groups": []},
            "change": {"users": sorted(set(change_users)), "groups": []},
        }
        return self._request(
            "POST",
            "/api/documents/bulk_edit/",
            body={
                "documents": document_ids,
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

    def paperless_login_url(self, document_id):
        """Open the private archive through its normal SSO boundary.

        Paperless keeps the requested document as the post-login destination,
        so an existing Pocket ID session returns directly to the archive copy.
        """
        detail_path = f"/documents/{int(document_id)}/details"
        return (
            f"{self.public_url}/accounts/login/?next="
            f"{urllib.parse.quote(detail_path, safe='')}"
        )
