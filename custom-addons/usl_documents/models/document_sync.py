"""Paperless synchronization, metadata catalogs, classification reconciliation and integrity diagnostics."""

import json
import logging
from datetime import UTC, datetime, timedelta

from odoo import (
    Command,
    _,
    api,
    fields,
    models,
)

from .paperless_client import PaperlessError, PaperlessNotFound

_logger = logging.getLogger(__name__)


class UslDocument(models.Model):
    _inherit = "usl.document"

    @api.model
    def diagnostics(self):
        self._require_manager()
        values = {
            "configured": self._paperless().configured,
            "last_sync": self.env["ir.config_parameter"].sudo().get_str(
                "usl_documents.last_sync",
            ),
            "sync_status": self.env["ir.config_parameter"].sudo().get_str(
                "usl_documents.sync_status", "unknown",
            ),
            "sync_error": self.env["ir.config_parameter"].sudo().get_str(
                "usl_documents.sync_error",
            ),
            "sync_cursor_page": self.env["ir.config_parameter"].sudo().get_str(
                "usl_documents.sync_cursor_page",
            ),
            "cached_documents": self.search_count([]),
            "missing_documents": self.search_count(
                [("availability_state", "=", "missing")],
            ),
            "trashed_documents": self.search_count(
                [("availability_state", "=", "trashed")],
            ),
            "permission_failures": self.search_count(
                [("permission_sync_state", "=", "failed")],
            ),
        }
        if values["configured"]:
            try:
                values.update(self._paperless().compatibility())
            except PaperlessError as error:
                values.update({"ok": False, "error": str(error)})
        return values

    @api.model
    def _paperless_datetime(self, value):
        if not value:
            return False
        if isinstance(value, datetime):
            parsed = value
        else:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo:
            parsed = parsed.astimezone(UTC).replace(tzinfo=None)
        return fields.Datetime.to_string(parsed)

    @api.model
    def _paperless_values(
        self,
        payload,
        *,
        source="paperless",
        metadata_catalog=None,
        metadata_records=None,
    ):
        metadata_catalog = metadata_catalog or {}
        metadata_records = metadata_records if metadata_records is not None else {}

        def metadata_name(section, value, fallback=False):
            try:
                key = int(value)
            except (TypeError, ValueError):
                key = value
            return metadata_catalog.get(section, {}).get(key, fallback)

        def metadata_record(model_name, value):
            remote_id = value.get("id") if isinstance(value, dict) else value
            try:
                remote_id = int(remote_id)
            except (TypeError, ValueError):
                return self.env[model_name]
            records_by_id = metadata_records.get(model_name)
            record = (
                records_by_id.get(remote_id, self.env[model_name])
                if records_by_id is not None
                else self.env[model_name].sudo().search(
                    [("paperless_id", "=", remote_id)], limit=1,
                )
            )
            if not record and isinstance(value, dict):
                record = (
                    self.env[model_name]
                    .sudo()
                    .with_context(usl_documents_cache_write=True)
                    .create(self.env[model_name]._cache_values(value))
                )
                if records_by_id is not None:
                    records_by_id[remote_id] = record
            return record

        tags = payload.get("tags") or []
        tag_records = self.env["usl.paperless.tag"]
        for tag in tags:
            tag_records |= metadata_record("usl.paperless.tag", tag)
        correspondent = payload.get("correspondent")
        document_type = payload.get("document_type")
        correspondent_record = metadata_record(
            "usl.paperless.correspondent", correspondent,
        )
        document_type_record = metadata_record(
            "usl.paperless.document.type", document_type,
        )
        correspondent_name = (
            correspondent_record.name
            or (
                correspondent.get("name")
                if isinstance(correspondent, dict)
                else metadata_name("correspondents", correspondent)
            )
        )
        document_type_name = (
            document_type_record.name
            or (
                document_type.get("name")
                if isinstance(document_type, dict)
                else metadata_name("document_types", document_type)
            )
        )
        versions = payload.get("versions") or []
        current_version = (
            versions[0] if versions and isinstance(versions[0], dict) else {}
        )
        return {
            "name": payload.get("title") or _("Untitled document"),
            "paperless_id": int(payload["id"]),
            "paperless_created": self._paperless_datetime(payload.get("added")),
            "paperless_modified": self._paperless_datetime(payload.get("modified")),
            "document_date": payload.get("created"),
            "original_filename": current_version.get("original_file_name")
            or current_version.get("original_filename")
            or payload.get("original_file_name")
            or payload.get("original_filename"),
            "mime_type": payload.get("mime_type"),
            # Paperless API v10 exposes the current file first. The cache's
            # document checksum follows that current version, while every
            # historical checksum (including the received original) is kept
            # on usl.document.version.
            "checksum": current_version.get("checksum") or payload.get("checksum"),
            "archive_checksum": payload.get("archive_checksum"),
            "correspondent_id": correspondent_record.id or False,
            "document_type_id": document_type_record.id or False,
            "tag_ids": [Command.set(tag_records.ids)],
            "correspondent_name": correspondent_name,
            "document_type_name": document_type_name,
            "custom_fields_json": json.dumps(
                payload.get("custom_fields") or [], sort_keys=True,
            ),
            "current_version_label": current_version.get("version_label"),
            "availability_state": "available",
            "source": source,
            "last_error": False,
        }

    @api.model
    def _paperless_metadata_records(self):
        """Prefetch synchronized catalogs once for a multi-document refresh."""
        return {
            model_name: {
                record.paperless_id: record
                for record in self.env[model_name].sudo().search([])
            }
            for model_name in (
                "usl.paperless.tag",
                "usl.paperless.correspondent",
                "usl.paperless.document.type",
            )
        }

    @api.model
    def _sync_metadata_catalogs(self, client):
        """Refresh supported Paperless catalogs and bind the curated views."""
        for model_name in (
            "usl.paperless.tag",
            "usl.paperless.correspondent",
            "usl.paperless.document.type",
        ):
            self.env[model_name].synchronize_catalog(client=client)
        self._configure_archive_automation(client)
        custom_fields = [
            {
                "id": int(item["id"]),
                "name": item["name"],
                "data_type": item["data_type"],
                "extra_data": item.get("extra_data"),
            }
            for item in client.list_custom_fields()
            if not (item.get("name") or "").startswith("Legacy Odoo ")
        ]
        self.env["ir.config_parameter"].sudo().set_str(
            "usl_documents.paperless_custom_fields",
            json.dumps(custom_fields, sort_keys=True),
        )

        tag_model = self.env["usl.paperless.tag"].sudo()
        default_tags = {
            "contracts": ("Contracts & legal", "#8c6bb1"),
            "banking": ("Banking", "#2b8cbe"),
            "tax": ("Tax & reporting", "#31a354"),
        }
        created = False
        for _key, (name, color) in default_tags.items():
            if not tag_model.search([("name", "=ilike", name)], limit=1):
                client.create_metadata(
                    "tags",
                    {
                        **tag_model._paperless_payload({
                            "name": name,
                            "color": color,
                            "matching_algorithm": 6,
                            "match": "",
                            "is_insensitive": True,
                        }),
                        "owner": None,
                    },
                )
                created = True
        if created:
            tag_model.synchronize_catalog(client=client)
        smart_views = self.env["usl.document.smart.view"].sudo()
        for key, (name, _color) in default_tags.items():
            view = smart_views.search([("key", "=", key)], limit=1)
            tag = tag_model.search([("name", "=ilike", name)], limit=1)
            if view and tag and (
                tag not in view.tag_ids or not view.archive_native
            ):
                view.with_context(usl_documents_view_setup=True).write(
                    {
                        "tag_ids": [Command.link(tag.id)],
                        "archive_native": True,
                    },
                )
        smart_views.synchronize_archive_views(client=client)

    @api.model
    def _configure_archive_automation(self, client):
        """Enable Paperless learning when the catalog has usable examples.

        Explicit matching rules are user-owned and are never replaced.  Automatic
        learning is enabled only for shared, active metadata that is still
        unconfigured and already occurs on at least two documents.
        """
        configured = 0
        for model_name in (
            "usl.paperless.tag",
            "usl.paperless.correspondent",
            "usl.paperless.document.type",
        ):
            model = self.env[model_name].sudo()
            records = model.search(
                [
                    ("active", "=", True),
                    ("matching_algorithm", "=", "0"),
                    ("match", "in", (False, "")),
                    ("document_count", ">=", 2),
                ],
            )
            if model_name == "usl.paperless.tag":
                records = records.filtered(lambda record: not record.is_inbox_tag)
            for record in records:
                payload = client.update_metadata(
                    model._paperless_kind,
                    record.paperless_id,
                    {
                        "matching_algorithm": 6,
                        "match": "",
                        "is_insensitive": True,
                    },
                )
                record.with_context(usl_documents_cache_write=True).write(
                    model._cache_values(payload),
                )
                configured += 1
        return configured

    @api.model
    def reconcile_linked_classification(self, *, limit=1000):
        """Finish classification backed by authoritative Odoo context.

        A mandatory evidence relationship or a direct-record/final-output
        relationship already carries the owning workflow's reviewed business
        context.  Those documents do not need a duplicate Documents approval.
        Manual workspace links remain ready for an explicit human review.
        """
        candidates = self.sudo().search(
            [
                ("review_state", "in", ("needs_attention", "classified")),
                ("availability_state", "=", "available"),
                ("company_id", "!=", False),
                ("permission_sync_state", "=", "synchronized"),
                ("last_error", "=", False),
                ("link_ids.active", "=", True),
                "|",
                ("document_type_id", "!=", False),
                ("tag_ids", "!=", False),
            ],
            order="id",
            limit=max(0, int(limit or 0)) or None,
        )
        classified = self.browse()
        reviewed = self.browse()
        skipped = 0
        for document in candidates:
            links = document.link_ids.filtered("active")
            if not links or any(
                link.company_id != document.company_id
                or link.policy_reason in {
                    "legacy_relationship_backfill_pending",
                    "legacy_operation_backfill_pending",
                }
                or link.res_model not in self.env
                or not self.env[link.res_model].sudo().browse(link.res_id).exists()
                for link in links
            ):
                skipped += 1
                continue
            has_authoritative_context = any(
                (
                    link.archive_mode == "mandatory"
                    and link.policy_role == "evidence"
                )
                or link.attachment_origin in {"direct_record", "generated_final"}
                for link in links
            )
            if has_authoritative_context:
                reviewed |= document
            elif document.review_state == "needs_attention":
                classified |= document
        if classified:
            classified.with_context(usl_documents_cache_write=True).write(
                {"review_state": "classified"},
            )
        if reviewed:
            reviewed.with_context(usl_documents_cache_write=True).write(
                {"review_state": "reviewed"},
            )
        return {
            "considered": len(candidates),
            "classified": len(classified),
            "reviewed": len(reviewed),
            "skipped": skipped,
        }

    @api.model
    def cron_reconcile_linked_classification(self):
        return self.reconcile_linked_classification(limit=0)

    @api.model
    def sync_from_paperless(self, *, full=False, limit_pages=None, client=None):
        self._require_manager()
        client = client or self._paperless()
        params = self.env["ir.config_parameter"].sudo()
        # Keep microseconds in the Paperless checkpoint. Odoo's database
        # datetime representation is second-granular; truncating here can omit
        # a document completed later in the same second as the sync starts.
        now = datetime.now(UTC).isoformat().replace("+00:00", "Z")
        page, modified_after, checkpoint = self._sync_open_checkpoint(params, full, now)

        seen = set()
        trashed_ids = set()
        touched = self.browse()
        pages_processed = 0
        complete = False
        metadata_catalog = None
        try:
            client.compatibility()
            self._sync_metadata_catalogs(client)
            metadata_records = self._paperless_metadata_records()
            while True:
                payload = client.list_documents(
                    page=page,
                    page_size=100,
                    modified_after=modified_after,
                    modified_before=checkpoint,
                )
                results = payload.get("results", [])
                if metadata_catalog is None and self._needs_metadata_catalog(results):
                    metadata_catalog = client.metadata_catalog()
                touched |= self._sync_active_documents(
                    results, metadata_catalog, metadata_records,
                )
                seen.update(int(item["id"]) for item in results)
                pages_processed += 1
                if not payload.get("next"):
                    complete = True
                    break
                page += 1
                if limit_pages and pages_processed >= int(limit_pages):
                    params.set_str("usl_documents.sync_cursor_page", str(page))
                    break

            if complete:
                trashed_documents, trashed_ids = self._sync_trashed_documents(
                    client, params, metadata_catalog, metadata_records,
                )
                touched |= trashed_documents
            if full and complete:
                touched |= self._sync_confirm_missing_documents(
                    client, seen, trashed_ids, metadata_catalog, metadata_records,
                )
            if touched:
                touched.filtered(
                    lambda item: (
                        item.availability_state != "trashed"
                        and item.permission_sync_state != "synchronized"
                    ),
                ).with_user(self.env.ref("base.user_root")).action_sync_permissions()
            if complete:
                self._sync_close_checkpoint(params, checkpoint)
            return {
                "synchronized": len(seen),
                "trashed": len(trashed_ids),
                "pages": pages_processed,
                "complete": complete,
                "next_page": page if not complete else None,
                "checkpoint": checkpoint,
            }
        except PaperlessError as error:
            params.set_str("usl_documents.sync_status", "failed")
            params.set_str("usl_documents.sync_error", str(error))
            params.set_str("usl_documents.last_sync_error", now)
            if not full:
                params.set_str("usl_documents.sync_cursor_page", str(page))
            raise

    @api.model
    def _sync_open_checkpoint(self, params, full, now):
        """Resume or start a synchronization window and mark it as running."""
        cursor_mode = params.get_str("usl_documents.sync_mode")
        resuming = bool(
            not full
            and cursor_mode == "incremental"
            and params.get_str("usl_documents.sync_cursor_page"),
        )
        page = (
            int(params.get_str("usl_documents.sync_cursor_page") or 1)
            if resuming
            else 1
        )
        modified_after = (
            params.get_str("usl_documents.sync_modified_after")
            if resuming
            else (None if full else params.get_str("usl_documents.last_sync"))
        )
        checkpoint = (
            params.get_str("usl_documents.sync_checkpoint") if resuming else now
        )
        params.set_str("usl_documents.sync_status", "running")
        params.set_str("usl_documents.sync_error", "")
        params.set_str("usl_documents.sync_mode", "full" if full else "incremental")
        params.set_str("usl_documents.sync_checkpoint", checkpoint)
        params.set_str("usl_documents.sync_modified_after", modified_after or "")
        return page, modified_after, checkpoint

    @api.model
    def _sync_close_checkpoint(self, params, checkpoint):
        """Record a completed synchronization and clear the resume cursor."""
        params.set_str("usl_documents.last_sync", checkpoint)
        params.set_str("usl_documents.sync_cursor_page", "")
        params.set_str("usl_documents.sync_checkpoint", "")
        params.set_str("usl_documents.sync_modified_after", "")
        params.set_str("usl_documents.sync_mode", "")
        params.set_str("usl_documents.sync_status", "healthy")
        params.set_str("usl_documents.last_sync_error", "")

    @api.model
    def _needs_metadata_catalog(self, items):
        """Return whether Paperless returned metadata as bare identifiers."""
        return any(
            isinstance(item.get("correspondent"), int)
            or isinstance(item.get("document_type"), int)
            or any(isinstance(tag, int) for tag in (item.get("tags") or []))
            for item in items
        )

    @api.model
    def _sync_active_documents(self, results, metadata_catalog, metadata_records):
        """Refresh the cache for one page of active Paperless documents."""
        touched = self.browse()
        documents_by_paperless_id = {
            document.paperless_id: document
            for document in self.sudo().search(
                [
                    (
                        "paperless_id",
                        "in",
                        [int(item["id"]) for item in results],
                    ),
                ],
            )
        }
        for item in results:
            paperless_id = int(item["id"])
            document = documents_by_paperless_id.get(paperless_id)
            values = self._paperless_values(
                item,
                metadata_catalog=metadata_catalog,
                metadata_records=metadata_records,
            )
            # A document returned by the active endpoint has left
            # Paperless Trash. Clear the previous deletion event even
            # when it was restored directly in Paperless; otherwise a
            # later Trash event could be falsely attributed to the
            # Odoo user who performed the earlier one.
            values.update(
                {
                    "trashed_at": False,
                    "trashed_by_id": False,
                    "trashed_by_label": False,
                    "retention_until": False,
                    "deletion_approved_by_id": False,
                    "deletion_approved_at": False,
                    "deletion_reason": False,
                },
            )
            if document:
                # Odoo-origin provenance is authoritative and must survive
                # refreshes of the Paperless metadata cache.
                values.pop("source", None)
                document.with_context(
                    usl_documents_cache_write=True,
                    skip_permission_invalidation=True,
                ).write(values)
            else:
                document = self.sudo().create(values)
                documents_by_paperless_id[paperless_id] = document
            if document.source == "paperless":
                document._merge_original_timestamps(
                    document.paperless_created,
                    document.paperless_modified,
                )
            document._synchronize_versions(item.get("versions") or [])
            touched |= document
        return touched

    @api.model
    def _sync_trashed_documents(self, client, params, metadata_catalog, metadata_records):
        """Mirror Paperless Trash and return the touched documents and their ids."""
        retention_days = max(
            0,
            params.get_int(
                "usl_documents.paperless_trash_retention_days",
                30,
            ),
        )
        touched = self.browse()
        trashed_ids = set()
        trashed_items = list(client.list_trashed_documents())
        trashed_documents_by_paperless_id = {
            document.paperless_id: document
            for document in self.sudo().search(
                [
                    (
                        "paperless_id",
                        "in",
                        [int(item["id"]) for item in trashed_items],
                    ),
                ],
            )
        }
        for item in trashed_items:
            paperless_id = int(item["id"])
            trashed_ids.add(paperless_id)
            document = trashed_documents_by_paperless_id.get(paperless_id)
            values = self._paperless_values(
                item,
                metadata_catalog=metadata_catalog,
                metadata_records=metadata_records,
            )
            values["availability_state"] = "trashed"
            values["last_error"] = False
            values.update(
                {
                    "permission_sync_state": "pending",
                    "permission_sync_error": False,
                    "permission_checked_at": False,
                },
            )
            trashed_at = self._paperless_datetime(item.get("deleted_at"))
            values["trashed_at"] = trashed_at
            if (
                not document
                or (
                    not document.trashed_by_id
                    and not document.trashed_by_label
                )
            ):
                values["trashed_by_label"] = _(
                    "Moved in Paperless (user not provided by its API)",
                )
            values["retention_until"] = (
                fields.Datetime.to_datetime(trashed_at)
                + timedelta(days=retention_days)
                if trashed_at
                else False
            )
            if document and (
                document.accounting_evidence
                or document.confidentiality == "hr"
            ):
                values["retention_hold"] = True
            if document:
                values.pop("source", None)
                document.with_context(
                    usl_documents_cache_write=True,
                ).write(values)
            else:
                document = self.sudo().create(values)
                trashed_documents_by_paperless_id[paperless_id] = document
                document.with_context(
                    usl_documents_cache_write=True,
                ).write({"availability_state": "trashed"})
            if document.source == "paperless":
                document._merge_original_timestamps(
                    document.paperless_created,
                    document.paperless_modified,
                )
            document._synchronize_versions(item.get("versions") or [])
            touched |= document
        return touched, trashed_ids

    @api.model
    def _sync_confirm_missing_documents(
        self, client, seen, trashed_ids, metadata_catalog, metadata_records,
    ):
        """After a full listing, downgrade documents Paperless no longer returns."""
        touched = self.browse()
        omitted_available = self.sudo().search(
            [
                ("paperless_id", "not in", list(seen | trashed_ids)),
                ("availability_state", "=", "available"),
            ],
        )
        confirmed_missing = omitted_available
        # Paperless's list/search index is eventually consistent just
        # after consumption. An Odoo-origin document has already been
        # confirmed by its asynchronous task and direct document API;
        # do not downgrade it to missing (and thereby defeat local
        # checksum reuse) solely because one full-list response lags.
        # A direct supported-API lookup distinguishes that race from a
        # genuinely removed archive object.
        for document in omitted_available.filtered(
            lambda item: item.source != "paperless",
        ):
            try:
                item = client.get_document(document.paperless_id)
            except PaperlessNotFound:
                continue
            confirmed_missing -= document
            seen.add(document.paperless_id)
            if metadata_catalog is None and self._needs_metadata_catalog([item]):
                metadata_catalog = client.metadata_catalog()
            values = self._paperless_values(
                item,
                metadata_catalog=metadata_catalog,
                metadata_records=metadata_records,
            )
            values.pop("source", None)
            document.with_context(
                usl_documents_cache_write=True,
                skip_permission_invalidation=True,
            ).write(values)
            document._synchronize_versions(item.get("versions") or [])
            touched |= document
        confirmed_missing.with_context(
            usl_documents_cache_write=True,
        ).write(
            {
                "availability_state": "missing",
                "last_error": _(
                    "Document was not returned by a full Paperless reconciliation.",
                ),
                "permission_sync_state": "pending",
                "permission_sync_error": False,
                "permission_checked_at": False,
            },
        )
        self.sudo().search(
            [
                ("paperless_id", "not in", list(seen | trashed_ids)),
                ("availability_state", "=", "trashed"),
            ],
        ).with_context(usl_documents_cache_write=True).write(
            {
                "availability_state": "permanently_deleted",
                "permanently_deleted_at": fields.Datetime.now(),
                "last_error": _(
                    "Paperless no longer returns this previously trashed "
                    "archive item. Its Odoo tombstone and audit history "
                    "were retained.",
                ),
                "permission_sync_state": "pending",
                "permission_sync_error": False,
                "permission_checked_at": False,
            },
        )
        return touched

    @api.model
    def cron_sync_from_paperless(self):
        self._require_manager()
        try:
            client = self._paperless()
            self.env[
                "usl.paperless.user.mapping"
            ]._reconcile_remote_identity_state(client=client)
            result = self.sync_from_paperless(limit_pages=20, client=client)
            if result.get("complete"):
                result["classification"] = self.reconcile_linked_classification(
                    limit=1000,
                )
            return result
        except PaperlessError:
            _logger.exception("Paperless incremental synchronization failed")
            return False

    @api.model
    def integrity_manifest(self, backup_id=None):
        """Return a portable cross-system backup/reconciliation manifest."""
        self._require_manager()
        documents = self.search([])
        links = self.env["usl.document.link"].search([("active", "=", True)])
        orphaned_links = []
        for link in links:
            record = (
                self.env[link.res_model].browse(link.res_id).exists()
                if link.res_model in self.env
                else False
            )
            if not record:
                orphaned_links.append(link.id)
        compatibility = self._paperless().compatibility()
        remote = {}
        page = 1
        while True:
            payload = self._paperless().list_documents(page=page, page_size=100)
            for item in payload.get("results", []):
                remote[int(item["id"])] = item
            if not payload.get("next"):
                break
            page += 1
        trashed_remote = {
            int(item["id"]): item
            for item in self._paperless().list_trashed_documents()
        }
        remote.update(trashed_remote)
        live_documents = documents.filtered(
            lambda item: item.availability_state != "permanently_deleted",
        )
        tombstones = documents - live_documents
        mirrored_ids = set(live_documents.mapped("paperless_id"))
        remote_ids = set(remote)
        checksum_mismatches = []
        for document in live_documents.filtered("checksum"):
            payload = remote.get(document.paperless_id)
            if not payload:
                continue
            remote_checksums = {
                payload.get("checksum"),
                *[
                    version.get("checksum")
                    for version in (payload.get("versions") or [])
                    if isinstance(version, dict)
                ],
            }
            if document.checksum not in remote_checksums:
                checksum_mismatches.append(
                    {
                        "paperless_id": document.paperless_id,
                        "odoo_checksum": document.checksum,
                        "paperless_checksums": sorted(
                            value for value in remote_checksums if value
                        ),
                    },
                )
        params = self.env["ir.config_parameter"].sudo()
        return {
            "schema": "usl-documents-integrity-v1",
            "backup_id": backup_id or fields.Datetime.now().strftime("%Y%m%dT%H%M%SZ"),
            "generated_at": fields.Datetime.to_string(fields.Datetime.now()),
            "odoo_version": self.env["ir.module.module"].search(
                [("name", "=", "base")], limit=1,
            ).latest_version,
            "paperless_version": compatibility["server_version"],
            "paperless_api_version": compatibility["api_version"],
            "paperless_document_count": compatibility["document_count"],
            "paperless_trash_count": len(trashed_remote),
            "paperless_total_count": len(remote),
            "odoo_document_count": len(documents),
            "odoo_live_document_count": len(live_documents),
            "permanent_deletion_tombstone_count": len(tombstones),
            "permanently_deleted_paperless_ids": sorted(
                tombstones.mapped("paperless_id"),
            ),
            "relationship_count": len(links),
            "relationship_counts_by_model": {
                model: len(links.filtered(lambda item, m=model: item.res_model == m))
                for model in sorted(set(links.mapped("res_model")))
            },
            "version_count": len(documents.mapped("version_ids")),
            "missing_document_ids": sorted(mirrored_ids - remote_ids),
            "unmirrored_paperless_ids": sorted(remote_ids - mirrored_ids),
            "orphaned_relationship_ids": orphaned_links,
            "checksum_mismatches": checksum_mismatches,
            "permission_sync_failures": live_documents.filtered(
                lambda item: item.permission_sync_state == "failed",
            ).mapped("paperless_id"),
            "representative_checksums": [
                {
                    "paperless_id": document.paperless_id,
                    "checksum": document.checksum,
                    "versions": [
                        {
                            "paperless_version_id": version.paperless_version_id,
                            "checksum": version.checksum,
                            "archive_checksum": version.archive_checksum,
                        }
                        for version in document.version_ids
                    ],
                }
                for document in live_documents.filtered("checksum")[:20]
            ],
            "last_successful_sync": params.get_str("usl_documents.last_sync", ""),
            "sync_status": params.get_str("usl_documents.sync_status", "unknown"),
            "backup_completion_status": params.get_str(
                "usl_documents.backup_completion_status", "not_recorded",
            ),
            "last_restore_test": params.get_str(
                "usl_documents.last_restore_test", "not_recorded",
            ),
            "integrity_ok": not (
                mirrored_ids - remote_ids
                or remote_ids - mirrored_ids
                or orphaned_links
                or checksum_mismatches
                or live_documents.filtered(
                    lambda item: item.permission_sync_state == "failed",
                )
            ),
        }
