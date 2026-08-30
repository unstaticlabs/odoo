import hashlib
import os
import re
import secrets
import uuid
from datetime import timedelta, timezone
from urllib.parse import urlsplit

from psycopg2 import IntegrityError

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError

_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")
_DEFAULT_TTL_SECONDS = 300
_MIN_TTL_SECONDS = 30
_MAX_TTL_SECONDS = 900


def _iso_utc(value):
    return value.replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z")


class UslDocumentDownloadGrant(models.Model):
    _name = "usl.document.download.grant"
    _description = "Short-lived Document Download Grant"
    _order = "issued_at desc, id desc"

    public_id = fields.Char(required=True, readonly=True, index=True, copy=False)
    token_hash = fields.Char(required=True, readonly=True, index=True, copy=False)
    database_name = fields.Char(required=True, readonly=True)
    issued_by_id = fields.Many2one(
        "res.users", readonly=True, index=True, ondelete="set null",
    )
    issued_by_odoo_id = fields.Integer(required=True, readonly=True, index=True)
    document_id = fields.Many2one(
        "usl.document", readonly=True, index=True, ondelete="set null",
    )
    document_odoo_id = fields.Integer(required=True, readonly=True, index=True)
    document_name = fields.Char(required=True, readonly=True)
    document_version_id = fields.Many2one(
        "usl.document.version", readonly=True, index=True, ondelete="set null",
    )
    document_version_odoo_id = fields.Integer(
        required=True, readonly=True, index=True,
    )
    paperless_document_id = fields.Integer(required=True, readonly=True)
    paperless_version_id = fields.Char(required=True, readonly=True)
    variant = fields.Selection(
        [("original", "Original"), ("archive", "Archive")],
        required=True,
        readonly=True,
    )
    operation = fields.Selection(
        [("download", "Download")],
        required=True,
        readonly=True,
        default="download",
    )
    company_id = fields.Many2one(
        "res.company", readonly=True, index=True, ondelete="set null",
    )
    company_odoo_id = fields.Integer(readonly=True)
    current_company_id = fields.Integer(required=True, readonly=True)
    allowed_company_ids_json = fields.Json(required=True, readonly=True)
    filename = fields.Char(required=True, readonly=True)
    mime_type = fields.Char(required=True, readonly=True)
    size_bytes = fields.Integer(readonly=True)
    checksum = fields.Char(readonly=True)
    issued_at = fields.Datetime(required=True, readonly=True, index=True)
    expires_at = fields.Datetime(required=True, readonly=True, index=True)
    revoked_at = fields.Datetime(readonly=True, index=True)
    revoked_by_id = fields.Many2one(
        "res.users", readonly=True, ondelete="set null",
    )
    revocation_reason = fields.Char(readonly=True)
    first_redeemed_at = fields.Datetime(readonly=True)
    last_redeemed_at = fields.Datetime(readonly=True)
    redemption_count = fields.Integer(readonly=True, default=0)
    last_denied_at = fields.Datetime(readonly=True)
    denial_count = fields.Integer(readonly=True, default=0)
    last_denial_code = fields.Char(readonly=True)

    _public_id_unique = models.Constraint(
        "UNIQUE(public_id)",
        "A document download grant identifier must be unique.",
    )
    _token_hash_unique = models.Constraint(
        "UNIQUE(token_hash)",
        "A document download grant token must be unique.",
    )

    @api.model
    def _find_token(self, raw_token):
        value = str(raw_token or "")
        if not _TOKEN_PATTERN.fullmatch(value):
            return self.browse()
        token_hash = hashlib.sha256(value.encode("ascii")).hexdigest()
        return self.sudo().search([("token_hash", "=", token_hash)], limit=1)

    def _audit(self, event_type, *, actor=None, denial_code=None):
        self.ensure_one()
        audit = self.env["usl.document.download.grant.audit"].sudo()
        if audit.search_count(
            [("grant_id", "=", self.id), ("event_type", "=", event_type)],
            limit=1,
        ):
            return
        try:
            with self.env.cr.savepoint():
                audit.create(
                    {
                        "grant_id": self.id,
                        "grant_public_id": self.public_id,
                        "event_type": event_type,
                        "occurred_at": fields.Datetime.now(),
                        "actor_id": (actor or self.issued_by_id or self.env.user).id,
                        "issued_by_odoo_id": self.issued_by_odoo_id,
                        "document_odoo_id": self.document_odoo_id,
                        "document_version_odoo_id": self.document_version_odoo_id,
                        "paperless_version_id": self.paperless_version_id,
                        "variant": self.variant,
                        "denial_code": denial_code or False,
                        "correlation_id": (
                            self.env.context.get("usl_correlation_id") or False
                        ),
                    },
                )
        except IntegrityError:
            pass

    def _record_denial(self, event_type, denial_code):
        self.ensure_one()
        now = fields.Datetime.now()
        self.env.cr.execute(
            """
                UPDATE usl_document_download_grant
                   SET last_denied_at = %s,
                       denial_count = denial_count + 1,
                       last_denial_code = %s
                 WHERE id = %s
            """,
            (now, denial_code, self.id),
        )
        self.invalidate_recordset(
            ["last_denied_at", "denial_count", "last_denial_code"],
        )
        self._audit(event_type, denial_code=denial_code)

    def _record_redemption(self):
        self.ensure_one()
        now = fields.Datetime.now()
        self.env.cr.execute(
            """
                UPDATE usl_document_download_grant
                   SET first_redeemed_at = COALESCE(first_redeemed_at, %s),
                       last_redeemed_at = %s,
                       redemption_count = redemption_count + 1
                 WHERE id = %s
            """,
            (now, now, self.id),
        )
        self.invalidate_recordset(
            ["first_redeemed_at", "last_redeemed_at", "redemption_count"],
        )
        self._audit("redeemed")

    def _authorize_redemption(self):
        """Re-evaluate the bound binary using the current non-sudo environment."""
        self.ensure_one()
        snapshot = self.sudo()
        document = self.env["usl.document"].browse(
            snapshot.document_odoo_id,
        ).exists()
        version = self.env["usl.document.version"].browse(
            snapshot.document_version_odoo_id,
        ).exists()
        if not document or not version:
            raise AccessError(_("The bound document binary is unavailable."))
        descriptor = document._authorized_binary_descriptor(
            document_version_id=version.id,
            variant=snapshot.variant,
        )
        if (
            descriptor["paperless_document_id"] != snapshot.paperless_document_id
            or descriptor["paperless_version_id"] != snapshot.paperless_version_id
            or document.company_id.id != snapshot.company_odoo_id
            or (snapshot.checksum and descriptor["checksum"] != snapshot.checksum)
        ):
            raise AccessError(_("The bound document binary has changed."))
        return descriptor

    def _is_live_now(self):
        """Read revocation/expiry afresh so concurrent changes take effect."""
        self.ensure_one()
        current = self.sudo().exists()
        if not current:
            return False
        current.invalidate_recordset(["revoked_at", "expires_at"])
        return not current.revoked_at and current.expires_at > fields.Datetime.now()

    @api.model
    def cron_cleanup_download_grants(self):
        retention_days = self.env["ir.config_parameter"].sudo().get_int(
            "usl_documents.download_grant_audit_retention_days", 365,
        )
        retention_days = max(30, min(int(retention_days), 3650))
        cutoff = fields.Datetime.now() - timedelta(days=retention_days)
        expired = self.sudo().search([("expires_at", "<", cutoff)])
        expired.with_context(usl_documents_grant_cleanup=True).unlink()

    def unlink(self):
        if not self.env.context.get("usl_documents_grant_cleanup"):
            raise AccessError(_("Document download grants are audit records."))
        return super().unlink()


class UslDocumentDownloadGrantAudit(models.Model):
    _name = "usl.document.download.grant.audit"
    _description = "Document Download Grant Audit Event"
    _order = "occurred_at desc, id desc"

    grant_id = fields.Many2one(
        "usl.document.download.grant",
        required=True,
        readonly=True,
        index=True,
        ondelete="cascade",
    )
    grant_public_id = fields.Char(required=True, readonly=True, index=True)
    event_type = fields.Selection(
        [
            ("issued", "Issued"),
            ("redeemed", "Redeemed"),
            ("revoked", "Revoked"),
            ("denied_expired", "Denied: expired"),
            ("denied_revoked", "Denied: revoked"),
            ("denied_inactive", "Denied: inactive user"),
            ("denied_company", "Denied: company access"),
            ("denied_authorization", "Denied: authorization"),
            ("denied_file", "Denied: file unavailable"),
        ],
        required=True,
        readonly=True,
    )
    occurred_at = fields.Datetime(required=True, readonly=True, index=True)
    actor_id = fields.Many2one("res.users", readonly=True, ondelete="set null")
    issued_by_odoo_id = fields.Integer(readonly=True)
    document_odoo_id = fields.Integer(required=True, readonly=True)
    document_version_odoo_id = fields.Integer(required=True, readonly=True)
    paperless_version_id = fields.Char(required=True, readonly=True)
    variant = fields.Selection(
        [("original", "Original"), ("archive", "Archive")],
        required=True,
        readonly=True,
    )
    denial_code = fields.Char(readonly=True)
    correlation_id = fields.Char(readonly=True, index=True)

    _grant_event_unique = models.Constraint(
        "UNIQUE(grant_id, event_type)",
        "Each logical grant event is recorded once.",
    )

    def write(self, values):
        raise AccessError(_("Document download audit events are immutable."))

    def unlink(self):
        if not self.env.context.get("usl_documents_grant_cleanup"):
            raise AccessError(_("Document download audit events are immutable."))
        return super().unlink()


class UslDocumentDownloadService(models.Model):
    _inherit = "usl.document"

    def _authorized_binary_descriptor(self, *, document_version_id=None, variant):
        self.ensure_one()
        self.check_access("read")
        if not self._check_archive_binary_access():
            raise AccessError(_("The document binary is unavailable."))
        if variant not in ("original", "archive"):
            raise ValidationError(_("Unsupported document binary variant."))
        if document_version_id:
            version = self.env["usl.document.version"].browse(
                int(document_version_id),
            ).exists()
            if not version:
                raise ValidationError(_("The requested document version is unavailable."))
            version.check_access("read")
            if version.document_id != self:
                raise ValidationError(_("The requested document version is unavailable."))
        else:
            version = self.version_ids.filtered("is_current")[:1]
            if not version:
                raise ValidationError(_("The document has no current binary version."))
            version.check_access("read")
        if variant == "archive" and not version.archive_checksum:
            raise ValidationError(_("This document version has no archive binary."))
        filename = version.original_filename or self.original_filename or (
            f"document-{self.paperless_id}"
        )
        if variant == "archive" and not filename.lower().endswith(".pdf"):
            filename = f"{os.path.splitext(filename)[0]}.pdf"
        return {
            "document": self,
            "version": version,
            "paperless_document_id": self.paperless_id,
            "paperless_version_id": version.paperless_version_id,
            "variant": variant,
            "filename": filename,
            "mime_type": (
                "application/pdf"
                if variant == "archive"
                else version.mime_type or self.mime_type or "application/octet-stream"
            ),
            "checksum": (
                version.archive_checksum if variant == "archive" else version.checksum
            )
            or False,
        }

    @api.model
    def _canonical_download_base_url(self):
        params = self.env["ir.config_parameter"].sudo()
        base_url = params.get_str("web.base.url", "").strip().rstrip("/")
        frozen = params.get_str("web.base.url.freeze", "").strip().lower()
        parsed = urlsplit(base_url)
        if (
            frozen not in ("1", "true", "yes", "on")
            or parsed.scheme != "https"
            or not parsed.netloc
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in ("", "/")
        ):
            raise ValidationError(
                _("Document materialization requires a frozen canonical HTTPS Odoo URL."),
            )
        return base_url

    @api.model
    def mcp_create_download_grant(
        self,
        document_id,
        *,
        document_version_id=None,
        variant="original",
        ttl_seconds=_DEFAULT_TTL_SECONDS,
    ):
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int):
            raise ValidationError(_("Invalid document download lifetime."))
        if not _MIN_TTL_SECONDS <= ttl_seconds <= _MAX_TTL_SECONDS:
            raise ValidationError(
                _("Document download lifetime must be between 30 and 900 seconds."),
            )
        if not self.env.user.active or self.env.user._is_public():
            raise AccessError(_("An active authenticated user is required."))
        canonical_base_url = self._canonical_download_base_url()
        document = self._mcp_visible_document(document_id)
        descriptor = document._authorized_binary_descriptor(
            document_version_id=document_version_id,
            variant=variant,
        )
        probe = document._paperless().probe_download(
            descriptor["paperless_document_id"],
            version_id=descriptor["paperless_version_id"],
            original=variant == "original",
        )
        if probe["status"] != 200:
            raise ValidationError(_("The requested Paperless binary is unavailable."))
        headers = {key.lower(): value for key, value in probe["headers"].items()}
        upstream_etag = headers.get("etag", "").strip().strip('"')
        if (
            descriptor["checksum"]
            and upstream_etag
            and upstream_etag != descriptor["checksum"]
        ):
            raise ValidationError(
                _("The Paperless binary does not match the selected document version."),
            )
        try:
            size_bytes = int(headers.get("content-length", ""))
        except (TypeError, ValueError):
            size_bytes = False
        if size_bytes is not False and size_bytes < 0:
            size_bytes = False
        token = secrets.token_urlsafe(32)
        token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
        issued_at = fields.Datetime.now()
        allowed_company_ids = sorted(self.env.companies.ids)
        if document.company_id and document.company_id.id not in allowed_company_ids:
            raise AccessError(_("The document company is unavailable."))
        grant = self.env["usl.document.download.grant"].sudo().create(
            {
                "public_id": str(uuid.uuid4()),
                "token_hash": token_hash,
                "database_name": self.env.cr.dbname,
                "issued_by_id": self.env.user.id,
                "issued_by_odoo_id": self.env.user.id,
                "document_id": document.id,
                "document_odoo_id": document.id,
                "document_name": document.display_name,
                "document_version_id": descriptor["version"].id,
                "document_version_odoo_id": descriptor["version"].id,
                "paperless_document_id": descriptor["paperless_document_id"],
                "paperless_version_id": descriptor["paperless_version_id"],
                "variant": variant,
                "operation": "download",
                "company_id": document.company_id.id,
                "company_odoo_id": document.company_id.id,
                "current_company_id": self.env.company.id,
                "allowed_company_ids_json": allowed_company_ids,
                "filename": descriptor["filename"],
                "mime_type": descriptor["mime_type"],
                "size_bytes": size_bytes,
                "checksum": descriptor["checksum"],
                "issued_at": issued_at,
                "expires_at": issued_at + timedelta(seconds=ttl_seconds),
            },
        )
        grant._audit("issued", actor=self.env.user)
        return {
            "grant_id": grant.public_id,
            "url": f"{canonical_base_url}/agent-documents/{token}",
            "expires_at": _iso_utc(grant.expires_at),
            "ttl_seconds": ttl_seconds,
            "document": {
                "id": document.id,
                "name": document.display_name,
            },
            "version": {
                "id": descriptor["version"].id,
                "paperless_version_id": descriptor["paperless_version_id"],
                "label": descriptor["version"].label,
                "is_current_at_issuance": descriptor["version"].is_current,
            },
            "variant": variant,
            "filename": descriptor["filename"],
            "mime_type": descriptor["mime_type"],
            "size_bytes": size_bytes,
            "checksum": descriptor["checksum"],
        }

    @api.model
    def mcp_revoke_download_grant(self, grant_id, *, reason=None):
        try:
            normalized_id = str(uuid.UUID(str(grant_id)))
        except (TypeError, ValueError, AttributeError) as error:
            raise ValidationError(_("Invalid document download grant identifier.")) from error
        grant = self.env["usl.document.download.grant"].sudo().search(
            [("public_id", "=", normalized_id)], limit=1,
        )
        if not grant:
            raise ValidationError(_("The document download grant is unavailable."))
        if (
            grant.issued_by_id.id != self.env.user.id
            and not self.env.user.has_group("usl_documents.group_documents_manager")
        ):
            raise AccessError(_("You may not revoke this document download grant."))
        reason = str(reason or "").strip()
        if len(reason) > 500:
            raise ValidationError(_("The revocation reason is too long."))
        if not grant.revoked_at:
            grant.write(
                {
                    "revoked_at": fields.Datetime.now(),
                    "revoked_by_id": self.env.user.id,
                    "revocation_reason": reason or False,
                },
            )
            grant._audit("revoked", actor=self.env.user)
        return {
            "grant_id": grant.public_id,
            "revoked": True,
            "revoked_at": _iso_utc(grant.revoked_at),
        }
