import base64
import hashlib
import json
import logging
import os
from datetime import UTC, datetime, time, timedelta
from urllib.parse import quote

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError

from .constants import INTERNAL_OPERATION
from odoo.addons.usl_sign.services import (
    DSSClient,
    DSSServiceError,
    OpenTimestampsClient,
    OpenTimestampsError,
    OpenTimestampsRejectedError,
    base64_text,
    field_content,
    field_value,
)

_logger = logging.getLogger(__name__)

MANIFEST_OPERATION_FIELDS = {
    "anchoring_status",
    "attempt_count",
    "consecutive_failures",
    "last_attempt_at",
    "next_attempt_at",
    "submitted_at",
    "failure_code",
    "failure_message",
    "submission_nonce",
    "initial_receipt_id",
    "latest_receipt_id",
    "confirmed_receipt_id",
    "bitcoin_block_height",
    "bitcoin_block_hash",
    "bitcoin_block_time",
    "bitcoin_confirmations",
    "confirmed_at",
    "verification_report",
    "verification_report_sha256",
    "proof_dossier",
    "proof_dossier_sha256",
    "archive_status",
    "archive_operation_id",
    "archive_document_id",
    "archive_error",
}


class SignDailyManifest(models.Model):
    _name = "usl.sign.daily.manifest"
    _description = "Signed Daily Signature Evidence Manifest"
    _order = "manifest_date desc, company_id, id desc"
    _rec_name = "name"

    name = fields.Char(compute="_compute_name")
    company_id = fields.Many2one(
        "res.company", required=True, index=True, ondelete="restrict",
    )
    manifest_date = fields.Date(required=True, index=True)
    state = fields.Selection(
        [("signed", "Signed"), ("failed", "Signing failed")],
        required=True,
        readonly=True,
    )
    previous_manifest_id = fields.Many2one(
        "usl.sign.daily.manifest", readonly=True, ondelete="restrict",
    )
    previous_manifest_sha256 = fields.Char(readonly=True)
    payload = fields.Binary(required=True, readonly=True)
    payload_filename = fields.Char(readonly=True)
    payload_sha256 = fields.Char(required=True, readonly=True, index=True)
    event_count = fields.Integer(required=True, readonly=True)
    request_count = fields.Integer(required=True, readonly=True)
    entry_ids = fields.One2many(
        "usl.sign.daily.manifest.entry", "manifest_id", readonly=True,
    )
    signature = fields.Binary(readonly=True)
    signature_algorithm = fields.Char(readonly=True)
    certificate_chain = fields.Json(readonly=True)
    signed_at = fields.Datetime(readonly=True)
    signed_envelope = fields.Binary(readonly=True)
    signed_envelope_filename = fields.Char(readonly=True)
    signed_envelope_sha256 = fields.Char(readonly=True, index=True)

    anchoring_status = fields.Selection(
        [
            ("scheduled", "Scheduled"),
            ("pending", "Awaiting confirmation"),
            ("confirmed", "Confirmed"),
            ("action_required", "Action required"),
        ],
        default="scheduled",
        required=True,
        readonly=True,
        index=True,
    )
    receipt_ids = fields.One2many(
        "usl.sign.daily.manifest.receipt", "manifest_id", readonly=True,
    )
    initial_receipt_id = fields.Many2one(
        "usl.sign.daily.manifest.receipt", readonly=True, ondelete="restrict",
    )
    latest_receipt_id = fields.Many2one(
        "usl.sign.daily.manifest.receipt", readonly=True, ondelete="restrict",
    )
    confirmed_receipt_id = fields.Many2one(
        "usl.sign.daily.manifest.receipt", readonly=True, ondelete="restrict",
    )
    attempt_count = fields.Integer(readonly=True)
    consecutive_failures = fields.Integer(readonly=True)
    last_attempt_at = fields.Datetime(readonly=True)
    next_attempt_at = fields.Datetime(readonly=True)
    submitted_at = fields.Datetime(readonly=True)
    submission_nonce = fields.Binary(readonly=True)
    failure_code = fields.Char(readonly=True)
    failure_message = fields.Char(readonly=True)
    bitcoin_block_height = fields.Integer(readonly=True)
    bitcoin_block_hash = fields.Char(readonly=True)
    bitcoin_block_time = fields.Datetime(readonly=True)
    bitcoin_confirmations = fields.Integer(readonly=True)
    confirmed_at = fields.Datetime(readonly=True)
    verification_report = fields.Binary(readonly=True)
    verification_report_filename = fields.Char(
        compute="_compute_artifact_filenames",
    )
    verification_report_sha256 = fields.Char(readonly=True)
    proof_dossier = fields.Binary(readonly=True)
    proof_dossier_filename = fields.Char(compute="_compute_artifact_filenames")
    proof_dossier_sha256 = fields.Char(readonly=True)
    archive_status = fields.Selection(
        [
            ("not_started", "Not archived"),
            ("processing", "Archiving"),
            ("archived", "Archived"),
            ("failed", "Archive failed"),
        ],
        default="not_started",
        required=True,
        readonly=True,
    )
    archive_operation_id = fields.Many2one(
        "usl.document.operation", readonly=True, ondelete="set null",
    )
    archive_document_id = fields.Many2one(
        "usl.document", readonly=True, ondelete="restrict",
    )
    archive_error = fields.Char(readonly=True)

    _company_day_unique = models.Constraint(
        "UNIQUE(company_id, manifest_date)",
        "A company can have only one daily Sign evidence manifest.",
    )

    @api.depends("manifest_date", "company_id")
    def _compute_name(self):
        for manifest in self:
            manifest.name = _("%(company)s — %(date)s") % {
                "company": manifest.company_id.display_name,
                "date": fields.Date.to_string(manifest.manifest_date),
            }

    @api.depends("manifest_date")
    def _compute_artifact_filenames(self):
        for manifest in self:
            day = fields.Date.to_string(manifest.manifest_date)
            manifest.verification_report_filename = (
                f"sign-evidence-{day}-opentimestamps-verification.json"
            )
            manifest.proof_dossier_filename = (
                f"sign-evidence-{day}-timestamp-proof.pdf"
            )

    @staticmethod
    def _canonical_json(payload, *, indent=None):
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":") if indent is None else None,
            ensure_ascii=False,
            indent=indent,
        ).encode()

    @api.model
    def _manifest_entries(self, company, manifest_date):
        start = datetime.combine(manifest_date, time.min)
        end = start + timedelta(days=1)
        events = self.env["usl.sign.event"].sudo().search(
            [
                ("company_id", "=", company.id),
                ("occurred_at", ">=", start),
                ("occurred_at", "<", end),
            ],
            order="request_id, sequence",
        )
        entries = []
        for sign_request in events.mapped("request_id"):
            sign_request.event_ids.verify_chain()
            day_events = events.filtered(lambda event: event.request_id == sign_request)
            day_head = day_events[-1:]
            completion = sign_request.event_ids.filtered(
                lambda event: event.event_type == "request_completed"
                and event.occurred_at < end,
            )[-1:]
            dossier = sign_request.evidence_ids.filtered(
                lambda evidence: evidence.kind == "dossier",
            )[-1:]
            completed_before_end = bool(
                sign_request.completed_at and sign_request.completed_at < end,
            )
            entries.append(
                {
                    "record_type": "signature",
                    "request_id": sign_request.id,
                    "request_reference": sign_request.name,
                    "event_sequence": day_head.sequence,
                    "event_hash": day_head.event_hash,
                    "chain_head_sequence": day_head.sequence,
                    "chain_head_hash": day_head.event_hash,
                    "final_sha256": sign_request.final_sha256 or None
                    if completed_before_end
                    else None,
                    "dossier_sha256": dossier.sha256 or None
                    if completed_before_end
                    else None,
                    "completion_event_sequence": completion.sequence or None,
                    "completion_event_hash": completion.event_hash or None,
                    "completed_at": fields.Datetime.to_string(sign_request.completed_at)
                    if completed_before_end
                    else None,
                },
            )
        return len(events), sorted(
            entries,
            key=lambda row: (row["request_reference"], row["request_id"]),
        )

    @api.model
    def _canonical_payload(self, company, manifest_date, previous):
        event_count, entries = self._manifest_entries(company, manifest_date)
        requests = [entry for entry in entries if entry["record_type"] == "signature"]
        payload = {
            "format": "usl-sign-daily-evidence-manifest-v4",
            "company_id": company.id,
            "manifest_date": fields.Date.to_string(manifest_date),
            "time_basis": "closed-utc-day",
            "previous_manifest_sha256": previous.signed_envelope_sha256 or None,
            "event_count": event_count,
            "request_count": len(requests),
            "requests": requests,
        }
        return self._canonical_json(payload), event_count, entries

    @staticmethod
    def _signed_envelope(raw, signed):
        envelope = {
            "format": "usl-sign-signed-daily-evidence-manifest-v1",
            "payload": base64.b64encode(raw).decode(),
            "payload_sha256": hashlib.sha256(raw).hexdigest(),
            "signature": signed["signature"],
            "signature_algorithm": signed["signatureAlgorithm"],
            "certificate_chain": signed["certificateChain"],
        }
        return SignDailyManifest._canonical_json(envelope)

    @api.model
    def _build_for_day(self, company, manifest_date):
        manifest_date = fields.Date.to_date(manifest_date)
        if manifest_date >= datetime.now(UTC).date():
            msg = "Only a closed UTC day can be manifested."
            raise ValidationError(msg)
        manifest = self.search(
            [("company_id", "=", company.id), ("manifest_date", "=", manifest_date)],
            limit=1,
        )
        if manifest:
            if manifest.state == "failed":
                manifest._retry_signing()
            return manifest
        previous = self.search(
            [
                ("company_id", "=", company.id),
                ("manifest_date", "<", manifest_date),
            ],
            order="manifest_date desc, id desc",
            limit=1,
        )
        if previous and previous.state != "signed":
            msg = "The preceding daily manifest must be signed before this day."
            raise ValidationError(msg)
        if previous and manifest_date != previous.manifest_date + timedelta(days=1):
            msg = "Daily manifests must be built without gaps."
            raise ValidationError(msg)
        raw, event_count, entries = self._canonical_payload(
            company,
            manifest_date,
            previous,
        )
        day = fields.Date.to_string(manifest_date)
        values = {
            "company_id": company.id,
            "manifest_date": manifest_date,
            "previous_manifest_id": previous.id,
            "previous_manifest_sha256": previous.signed_envelope_sha256,
            "payload": field_value(raw),
            "payload_filename": f"sign-evidence-{day}-manifest-payload.json",
            "payload_sha256": hashlib.sha256(raw).hexdigest(),
            "event_count": event_count,
            "request_count": len(
                [entry for entry in entries if entry["record_type"] == "signature"],
            ),
            "state": "failed",
            "anchoring_status": "action_required",
            "failure_code": "manifest_signing_not_attempted",
        }
        try:
            signed = DSSClient().sign_manifest(raw)
            envelope = self._signed_envelope(raw, signed)
            values.update(
                {
                    "state": "signed",
                    "signature": field_value(base64.b64decode(signed["signature"])),
                    "signature_algorithm": signed["signatureAlgorithm"],
                    "certificate_chain": signed["certificateChain"],
                    "signed_at": fields.Datetime.now(),
                    "signed_envelope": field_value(envelope),
                    "signed_envelope_filename": f"sign-evidence-{day}-signed-manifest.json",
                    "signed_envelope_sha256": hashlib.sha256(envelope).hexdigest(),
                    "anchoring_status": "scheduled",
                    "failure_code": False,
                    "failure_message": False,
                },
            )
        except (DSSServiceError, KeyError, ValueError, TypeError) as error:
            values.update(
                {
                    "failure_code": type(error).__name__,
                    "failure_message": "The daily evidence manifest could not be signed.",
                },
            )
        manifest = self.with_context(
            usl_sign_daily_manifest_build=INTERNAL_OPERATION,
        ).create(values)
        self.env["usl.sign.daily.manifest.entry"].with_context(
            usl_sign_daily_manifest_entry_create=INTERNAL_OPERATION,
        ).create(
            [
                {
                    "manifest_id": manifest.id,
                    **entry,
                }
                for entry in entries
            ],
        )
        return manifest

    def _retry_signing(self):
        for manifest in self:
            if manifest.state != "failed":
                msg = "Only a failed daily manifest can be signed again."
                raise ValidationError(msg)
            raw = field_content(manifest.payload)
            try:
                signed = DSSClient().sign_manifest(raw)
                envelope = self._signed_envelope(raw, signed)
            except (DSSServiceError, KeyError, ValueError, TypeError) as error:
                manifest.with_context(
                    usl_sign_daily_manifest_signing=INTERNAL_OPERATION,
                ).write(
                    {
                        "failure_code": type(error).__name__,
                        "failure_message": "The daily evidence manifest could not be signed.",
                    },
                )
                continue
            day = fields.Date.to_string(manifest.manifest_date)
            manifest.with_context(
                usl_sign_daily_manifest_signing=INTERNAL_OPERATION,
            ).write(
                {
                    "state": "signed",
                    "signature": field_value(base64.b64decode(signed["signature"])),
                    "signature_algorithm": signed["signatureAlgorithm"],
                    "certificate_chain": signed["certificateChain"],
                    "signed_at": fields.Datetime.now(),
                    "signed_envelope": field_value(envelope),
                    "signed_envelope_filename": f"sign-evidence-{day}-signed-manifest.json",
                    "signed_envelope_sha256": hashlib.sha256(envelope).hexdigest(),
                    "anchoring_status": "scheduled",
                    "failure_code": False,
                    "failure_message": False,
                },
            )

    @api.model
    def _cron_build_daily_manifests(self):
        closed_day = datetime.now(UTC).date() - timedelta(days=1)
        batch_days = 31
        for company in self.env["res.company"].sudo().search(
            [("sign_opentimestamps_enabled", "=", True)],
        ):
            latest = self.sudo().search(
                [("company_id", "=", company.id)],
                order="manifest_date desc, id desc",
                limit=1,
            )
            if latest:
                start_day = latest.manifest_date
                if latest.state == "failed":
                    latest._retry_signing()
                    if latest.state == "failed":
                        continue
                    start_day += timedelta(days=1)
                else:
                    start_day += timedelta(days=1)
            else:
                earliest_signature = self.env["usl.sign.event"].sudo().search(
                    [("company_id", "=", company.id)],
                    order="occurred_at, id",
                    limit=1,
                )
                first_dates = [
                    event.occurred_at.date()
                    for event in earliest_signature
                    if event.occurred_at
                ]
                start_day = min(
                    min(first_dates) if first_dates else closed_day,
                    closed_day,
                )
            current = start_day
            built = 0
            while current <= closed_day and built < batch_days:
                try:
                    manifest = self.sudo()._build_for_day(company, current)
                except (ValidationError, DSSServiceError):
                    _logger.exception(
                        "Daily Sign evidence manifest failed for company %s on %s",
                        company.id,
                        current,
                    )
                    break
                if manifest.state != "signed":
                    break
                built += 1
                current += timedelta(days=1)

    @staticmethod
    def _retry_delay(failures):
        minutes = min(360, 15 * (2 ** max(0, min(failures - 1, 5))))
        return timedelta(minutes=minutes)

    @staticmethod
    def _bitcoin_block_time(value):
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError) as error:
            msg = _("The verified Bitcoin block time is malformed.")
            raise OpenTimestampsRejectedError(
                msg,
                code="bitcoin_time_invalid",
            ) from error
        if parsed.tzinfo is None:
            msg = _("The verified Bitcoin block time has no UTC offset.")
            raise OpenTimestampsRejectedError(
                msg,
                code="bitcoin_time_invalid",
            )
        return parsed.astimezone(UTC).replace(tzinfo=None)

    def _operational_write(self, values):
        return self.with_context(
            usl_sign_daily_manifest_operation=INTERNAL_OPERATION,
        ).write(values)

    def _new_receipt(self, kind, raw):
        self.ensure_one()
        digest = hashlib.sha256(raw).hexdigest()
        existing = self.receipt_ids.filtered(lambda receipt: receipt.sha256 == digest)[:1]
        if existing:
            return existing
        return self.env["usl.sign.daily.manifest.receipt"].with_context(
            usl_sign_daily_manifest_receipt_create=INTERNAL_OPERATION,
        ).create(
            {
                "manifest_id": self.id,
                "sequence": len(self.receipt_ids) + 1,
                "kind": kind,
                "name": (
                    f"{self.signed_envelope_filename}.ots"
                    if kind == "submitted"
                    else f"{self.signed_envelope_filename}.upgraded-{len(self.receipt_ids)}.ots"
                ),
                "data": field_value(raw),
                "sha256": digest,
            },
        )

    def _record_anchoring_failure(self, error):
        self.ensure_one()
        failures = self.consecutive_failures + 1
        fatal = not getattr(error, "transient", False) or failures >= 8
        self._operational_write(
            {
                "anchoring_status": "action_required"
                if fatal
                else ("pending" if self.initial_receipt_id else "scheduled"),
                "attempt_count": self.attempt_count + 1,
                "consecutive_failures": failures,
                "last_attempt_at": fields.Datetime.now(),
                "next_attempt_at": False
                if fatal
                else fields.Datetime.now() + self._retry_delay(failures),
                "failure_code": getattr(error, "code", type(error).__name__),
                "failure_message": str(error)[:240],
            },
        )

    def _process_opentimestamps(self, client=None):
        self.ensure_one()
        if self.state != "signed" or not self.company_id.sign_opentimestamps_enabled:
            return False
        client = client or OpenTimestampsClient()
        now = fields.Datetime.now()
        if self.confirmed_at and self.verification_report:
            return self._complete_timestamp_proof_package(
                force_archive=self.archive_status == "failed",
            )
        envelope = field_content(self.signed_envelope)
        if not self.initial_receipt_id:
            if not self.submission_nonce:
                self._operational_write(
                    {"submission_nonce": field_value(os.urandom(16))},
                )
            nonce = field_content(self.submission_nonce)
            submitted = client.submit(envelope, nonce=nonce)
            receipt = self._new_receipt("submitted", submitted["receipt"])
            self._operational_write(
                {
                    "anchoring_status": "pending",
                    "initial_receipt_id": receipt.id,
                    "latest_receipt_id": receipt.id,
                    "submitted_at": now,
                    "attempt_count": self.attempt_count + 1,
                    "consecutive_failures": 0,
                    "last_attempt_at": now,
                    "next_attempt_at": now + timedelta(minutes=30),
                    "failure_code": False,
                    "failure_message": False,
                },
            )
            return True
        current = self.latest_receipt_id or self.initial_receipt_id
        upgraded = client.upgrade(field_content(current.data), envelope)
        if hashlib.sha256(upgraded["receipt"]).hexdigest() != current.sha256:
            current = self._new_receipt("upgraded", upgraded["receipt"])
            self._operational_write({"latest_receipt_id": current.id})
        values = {
            "anchoring_status": "pending",
            "attempt_count": self.attempt_count + 1,
            "consecutive_failures": 0,
            "last_attempt_at": now,
            "next_attempt_at": now + timedelta(minutes=30),
            "failure_code": False,
            "failure_message": False,
        }
        if upgraded["bitcoin_attestations"]:
            verification = client.verify(field_content(current.data), envelope)
            block_time = verification.get("bitcoin_block_time")
            values.update(
                {
                    "bitcoin_block_height": verification.get("bitcoin_block_height") or 0,
                    "bitcoin_block_hash": verification.get("bitcoin_block_hash") or False,
                    "bitcoin_block_time": self._bitcoin_block_time(block_time)
                    if block_time
                    else False,
                    "bitcoin_confirmations": verification.get("confirmations") or 0,
                },
            )
            if verification.get("status") == "confirmed":
                verification["verified_at"] = datetime.now(UTC).isoformat()
                verification["manifest_id"] = self.id
                report = self._canonical_json(verification, indent=2)
                values.update(
                    {
                        "anchoring_status": "confirmed",
                        "confirmed_receipt_id": current.id,
                        "confirmed_at": now,
                        "verification_report": field_value(report),
                        "verification_report_sha256": hashlib.sha256(report).hexdigest(),
                        "next_attempt_at": False,
                    },
                )
        self._operational_write(values)
        if self.anchoring_status == "confirmed":
            self._complete_timestamp_proof_package()
        return True

    def _complete_timestamp_proof_package(self, *, force_archive=False):
        self.ensure_one()
        try:
            if not self.proof_dossier:
                self._build_timestamp_dossier()
            archived = self._archive_timestamp_dossier(force=force_archive)
        except (DSSServiceError, UserError, ValidationError) as error:
            self._operational_write(
                {
                    "anchoring_status": "confirmed",
                    "archive_status": "failed",
                    "archive_error": "The confirmed daily proof dossier could not be prepared.",
                    "failure_code": type(error).__name__,
                    "failure_message": "Retry the daily proof archive after restoring the evidence services.",
                    "next_attempt_at": False,
                },
            )
            return False
        if archived:
            self._operational_write(
                {
                    "anchoring_status": "confirmed",
                    "failure_code": False,
                    "failure_message": False,
                    "next_attempt_at": False,
                },
            )
        return archived

    @api.model
    def _cron_process_opentimestamps(self):
        now = fields.Datetime.now()
        manifests = self.sudo().search(
            [
                ("state", "=", "signed"),
                ("company_id.sign_opentimestamps_enabled", "=", True),
                ("anchoring_status", "in", ["scheduled", "pending", "confirmed"]),
                ("archive_status", "!=", "archived"),
                "|",
                ("next_attempt_at", "=", False),
                ("next_attempt_at", "<=", now),
            ],
            order="manifest_date, company_id, id",
            limit=50,
        )
        for manifest in manifests:
            self.env.cr.execute(
                "SELECT id FROM usl_sign_daily_manifest WHERE id = %s FOR UPDATE SKIP LOCKED",
                [manifest.id],
            )
            if not self.env.cr.fetchone():
                continue
            manifest.invalidate_recordset()
            try:
                manifest._process_opentimestamps()
            except OpenTimestampsError as error:
                manifest._record_anchoring_failure(error)
            except (DSSServiceError, UserError, ValidationError) as error:
                manifest._record_anchoring_failure(
                    OpenTimestampsError(
                        "The timestamp proof package or archive could not be completed.",
                        code=type(error).__name__,
                    ),
                )
            except Exception as error:  # noqa: BLE001 -- cron must recover
                _logger.exception("Unexpected OpenTimestamps manifest failure")
                manifest._record_anchoring_failure(
                    OpenTimestampsError(
                        "The timestamp proof operation failed unexpectedly.",
                        code=type(error).__name__,
                        transient=True,
                    ),
                )

    def _build_timestamp_dossier(self):
        self.ensure_one()
        if not (
            self.signed_envelope
            and self.initial_receipt_id
            and self.confirmed_receipt_id
            and self.verification_report
        ):
            msg = "Confirmed timestamp evidence is incomplete."
            raise ValidationError(msg)
        instructions = (
            b"Independent verification\n\n"
            b"1. Extract the signed manifest JSON and confirmed .ots receipt.\n"
            b"2. Run: ots verify -f <signed-manifest.json> <receipt.ots> using a synced "
            b"Bitcoin Core node.\n"
            b"3. Verify the manifest's DSS signature and compare every listed SHA-256 "
            b"digest with its archived document.\n\n"
            b"OpenTimestamps proves existence no later than the attested Bitcoin block "
            b"time. It is not RFC 3161, a qualified timestamp, signer identification, "
            b"or proof that a document was signed at that exact time.\n"
        )
        artifacts = [
            {
                "name": self.signed_envelope_filename,
                "content": field_content(self.signed_envelope),
                "mimetype": "application/json",
                "relationship": "Data",
                "description": "DSS-signed daily evidence manifest",
            },
            {
                "name": self.initial_receipt_id.name,
                "content": field_content(self.initial_receipt_id.data),
                "mimetype": "application/vnd.opentimestamps.ots",
                "relationship": "Supplement",
                "description": "Original OpenTimestamps submission receipt",
            },
            {
                "name": self.confirmed_receipt_id.name,
                "content": field_content(self.confirmed_receipt_id.data),
                "mimetype": "application/vnd.opentimestamps.ots",
                "relationship": "Supplement",
                "description": "Upgraded portable Bitcoin timestamp proof",
            },
            {
                "name": self.verification_report_filename,
                "content": field_content(self.verification_report),
                "mimetype": "application/json",
                "relationship": "Supplement",
                "description": "Two-public-explorer verification report",
            },
            {
                "name": "VERIFY-OPENTIMESTAMPS.txt",
                "content": instructions,
                "mimetype": "text/plain",
                "relationship": "Supplement",
                "description": "Independent verification and legal-positioning notes",
            },
        ]
        dss = DSSClient()
        result = dss.build_dossier(
            title=f"Daily signature timestamp proof — {self.manifest_date}",
            summary=[
                f"Company: {self.company_id.name}",
                f"Closed UTC day: {self.manifest_date}",
                f"Signature requests covered: {self.request_count}",
                f"Events covered: {self.event_count}",
                f"Signed manifest SHA-256: {self.signed_envelope_sha256}",
                f"Bitcoin block: {self.bitcoin_block_height} ({self.bitcoin_block_hash})",
                f"Existence established no later than: {self.bitcoin_block_time}",
                "Verification: OpenTimestamps plus two agreeing public Bitcoin explorers",
                "This is independent existence evidence, not an RFC 3161 or qualified timestamp.",
            ],
            artifacts=artifacts,
        )
        dossier = base64.b64decode(result["document"])
        preflight = dss.validate_pdfa(dossier)
        if not preflight.get("compliant"):
            msg = "veraPDF rejected the timestamp proof dossier."
            raise DSSServiceError(msg)
        sealed = dss.seal(
            dossier,
            request_reference=f"USL-SIGN-OTS-{self.company_id.id}-{self.manifest_date}",
            timestamp=False,
        )
        dossier = base64.b64decode(sealed["document"])
        final_validation = dss.validate_pdfa(dossier)
        if not final_validation.get("compliant"):
            msg = "veraPDF rejected the sealed timestamp proof dossier."
            raise DSSServiceError(msg)
        self._operational_write(
            {
                "proof_dossier": field_value(dossier),
                "proof_dossier_sha256": hashlib.sha256(dossier).hexdigest(),
            },
        )

    def _archive_timestamp_dossier(self, force=False):
        self.ensure_one()
        if not self.proof_dossier:
            msg = "Build the timestamp proof dossier before archival."
            raise ValidationError(msg)
        if self.archive_status == "archived":
            return True
        if self.archive_status == "processing" and not force:
            return self._reconcile_timestamp_archive()
        archive_actor = self.env.ref("base.user_root")
        try:
            result = (
                self.env["usl.document"]
                .with_user(archive_actor)
                .sudo()
                .with_company(self.company_id)
                .upload_from_odoo(
                    self.proof_dossier_filename,
                    base64_text(field_content(self.proof_dossier)),
                    "application/pdf",
                    res_model=self._name,
                    res_id=self.id,
                    company_id=self.company_id.id,
                    confidentiality="private",
                    source="odoo_generated",
                )
            )
            if result.get("state") == "duplicate" and result.get("document_id"):
                values = {
                    "archive_status": "archived",
                    "archive_document_id": result["document_id"],
                    "archive_operation_id": False,
                    "archive_error": False,
                }
            elif result.get("operation_id"):
                values = {
                    "archive_status": "processing",
                    "archive_operation_id": result["operation_id"],
                    "archive_error": False,
                }
            else:
                self._operational_write(
                    {
                        "anchoring_status": "confirmed",
                        "archive_status": "failed",
                        "archive_error": "Paperless returned no archival relationship.",
                        "failure_code": "paperless_result_invalid",
                        "failure_message": "Retry timestamp proof archival after restoring Paperless.",
                    },
                )
                return False
        except Exception as error:  # noqa: BLE001 -- shared recovery UX
            self._operational_write(
                {
                    "anchoring_status": "confirmed",
                    "archive_status": "failed",
                    "archive_error": "Paperless could not archive the timestamp proof dossier.",
                    "failure_code": type(error).__name__,
                    "failure_message": "Retry timestamp proof archival after restoring Paperless.",
                },
            )
            return False
        self._operational_write(values)
        return self._reconcile_timestamp_archive()

    def _reconcile_timestamp_archive(self):
        self.ensure_one()
        operation = self.sudo().archive_operation_id
        if operation and operation.state == "processing":
            try:
                operation.poll()
                operation.invalidate_recordset()
            except Exception:  # noqa: BLE001 -- connector remains recoverable
                self._operational_write(
                    {
                        "anchoring_status": "confirmed",
                        "archive_status": "failed",
                        "archive_error": "Paperless archival status could not be confirmed.",
                    },
                )
                return False
        if operation and operation.state == "archived" and operation.document_id:
            self._operational_write(
                {
                    "anchoring_status": "confirmed",
                    "archive_status": "archived",
                    "archive_document_id": operation.document_id.id,
                    "archive_error": False,
                    "failure_code": False,
                    "failure_message": False,
                },
            )
        elif operation and operation.state == "failed":
            self._operational_write(
                {
                    "anchoring_status": "confirmed",
                    "archive_status": "failed",
                    "archive_error": "Paperless rejected the timestamp proof dossier.",
                },
            )
        return self.archive_status == "archived"

    def action_retry(self):
        failed = self.filtered(lambda manifest: manifest.state == "failed")
        if len(failed) != len(self):
            msg = "Only a failed daily manifest can be signed again."
            raise ValidationError(msg)
        failed.sudo()._retry_signing()
        return True

    def action_retry_anchoring(self):
        for manifest in self:
            if manifest.state != "signed" or manifest.anchoring_status != "action_required":
                msg = "Only a timestamp proof requiring action can be retried."
                raise ValidationError(msg)
            manifest.sudo()._operational_write(
                {
                    "anchoring_status": "pending"
                    if manifest.initial_receipt_id
                    else "scheduled",
                    "consecutive_failures": 0,
                    "next_attempt_at": False,
                    "failure_code": False,
                    "failure_message": False,
                },
            )
        return True

    def action_retry_archive(self):
        for manifest in self:
            if not manifest.confirmed_at or manifest.archive_status != "failed":
                msg = "Only a failed timestamp proof archive can be retried."
                raise ValidationError(msg)
            manifest.sudo()._complete_timestamp_proof_package(force_archive=True)
        return True

    def action_open_archive(self):
        self.ensure_one()
        if not self.archive_document_id:
            msg = "The timestamp proof dossier is not archived yet."
            raise UserError(msg)
        return {
            "type": "ir.actions.act_window",
            "res_model": "usl.document",
            "res_id": self.archive_document_id.id,
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "current",
        }

    def action_open_covered_request(self):
        self.ensure_one()
        msg = "Open a covered request from the Requests table below."
        raise UserError(msg)

    @api.model_create_multi
    def create(self, vals_list):
        if self.env.context.get("usl_sign_daily_manifest_build") is not INTERNAL_OPERATION:
            msg = "Daily manifests are created by the controlled signing job."
            raise AccessError(msg)
        return super().create(vals_list)

    def write(self, values):
        if self.env.context.get("usl_sign_daily_manifest_signing") is INTERNAL_OPERATION:
            allowed = {
                "state",
                "signature",
                "signature_algorithm",
                "certificate_chain",
                "signed_at",
                "signed_envelope",
                "signed_envelope_filename",
                "signed_envelope_sha256",
                "anchoring_status",
                "failure_code",
                "failure_message",
            }
            if set(values) <= allowed and all(manifest.state == "failed" for manifest in self):
                return super().write(values)
        if (
            self.env.context.get("usl_sign_daily_manifest_operation") is INTERNAL_OPERATION
            and set(values) <= MANIFEST_OPERATION_FIELDS
        ):
            return super().write(values)
        msg = "Daily evidence manifests and their proof artifacts are immutable."
        raise AccessError(msg)

    def unlink(self):
        msg = "Daily evidence manifests cannot be deleted."
        raise AccessError(msg)


class SignDailyManifestEntry(models.Model):
    _name = "usl.sign.daily.manifest.entry"
    _description = "Immutable Daily Signature Manifest Entry"
    _order = "manifest_id, request_reference"

    manifest_id = fields.Many2one(
        "usl.sign.daily.manifest", required=True, index=True, ondelete="cascade",
    )
    company_id = fields.Many2one(
        related="manifest_id.company_id", store=True, index=True,
    )
    record_type = fields.Selection(
        [("signature", "Signed document")], required=True, readonly=True,
    )
    request_id = fields.Many2one(
        "sign.oca.request", index=True, ondelete="restrict",
    )
    request_reference = fields.Char(required=True, readonly=True)
    event_sequence = fields.Integer(required=True, readonly=True)
    event_hash = fields.Char(required=True, readonly=True)
    chain_head_sequence = fields.Integer(required=True, readonly=True)
    chain_head_hash = fields.Char(required=True, readonly=True)
    final_sha256 = fields.Char(readonly=True)
    dossier_sha256 = fields.Char(readonly=True)
    completion_event_sequence = fields.Integer(readonly=True)
    completion_event_hash = fields.Char(readonly=True)
    completed_at = fields.Datetime(readonly=True)

    _manifest_request_unique = models.Constraint(
        "UNIQUE(manifest_id, request_id)",
        "A request can occur only once in a daily evidence manifest.",
    )

    @api.constrains("record_type", "request_id")
    def _check_record_binding(self):
        for entry in self:
            if entry.record_type != "signature" or not entry.request_id:
                msg = "A daily evidence entry must identify one signing request."
                raise ValidationError(msg)

    @api.model_create_multi
    def create(self, vals_list):
        if (
            self.env.context.get("usl_sign_daily_manifest_entry_create")
            is not INTERNAL_OPERATION
        ):
            msg = "Daily manifest entries use the controlled build operation."
            raise AccessError(msg)
        return super().create(vals_list)

    def write(self, values):
        msg = "Daily manifest entries are immutable."
        raise AccessError(msg)

    def unlink(self):
        msg = "Daily manifest entries cannot be deleted."
        raise AccessError(msg)


class SignDailyManifestReceipt(models.Model):
    _name = "usl.sign.daily.manifest.receipt"
    _description = "Immutable OpenTimestamps Receipt"
    _order = "manifest_id, sequence"

    manifest_id = fields.Many2one(
        "usl.sign.daily.manifest", required=True, index=True, ondelete="cascade",
    )
    company_id = fields.Many2one(
        related="manifest_id.company_id", store=True, index=True,
    )
    sequence = fields.Integer(required=True, readonly=True)
    kind = fields.Selection(
        [("submitted", "Original submission"), ("upgraded", "Upgraded proof")],
        required=True,
        readonly=True,
    )
    name = fields.Char(required=True, readonly=True)
    data = fields.Binary(required=True, readonly=True)
    sha256 = fields.Char(required=True, readonly=True, index=True)
    created_at = fields.Datetime(
        required=True, readonly=True, default=fields.Datetime.now,
    )

    _manifest_sequence_unique = models.Constraint(
        "UNIQUE(manifest_id, sequence)",
        "OpenTimestamps receipt sequence must be unique per manifest.",
    )
    _manifest_digest_unique = models.Constraint(
        "UNIQUE(manifest_id, sha256)",
        "An identical OpenTimestamps receipt is stored only once.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        if (
            self.env.context.get("usl_sign_daily_manifest_receipt_create")
            is not INTERNAL_OPERATION
        ):
            msg = "OpenTimestamps receipts use the controlled proof operation."
            raise AccessError(msg)
        for values in vals_list:
            try:
                raw = field_content(values.get("data"))
            except (TypeError, ValueError) as error:
                msg = "The OpenTimestamps receipt is not valid base64."
                raise ValidationError(msg) from error
            if not raw or len(raw) > 1024 * 1024:
                msg = "The OpenTimestamps receipt is empty or oversized."
                raise ValidationError(msg)
            digest = hashlib.sha256(raw).hexdigest()
            if values.get("sha256") != digest:
                msg = "The OpenTimestamps receipt checksum is invalid."
                raise ValidationError(msg)
        return super().create(vals_list)

    def write(self, values):
        msg = "OpenTimestamps receipts are immutable."
        raise AccessError(msg)

    def unlink(self):
        msg = "OpenTimestamps receipts cannot be deleted."
        raise AccessError(msg)


class SignRequestTimestampProof(models.Model):
    _inherit = "sign.oca.request"

    daily_manifest_entry_ids = fields.One2many(
        "usl.sign.daily.manifest.entry", "request_id", readonly=True,
    )
    daily_timestamp_manifest_id = fields.Many2one(
        "usl.sign.daily.manifest",
        compute="_compute_daily_timestamp_proof",
        compute_sudo=True,
    )
    daily_timestamp_status = fields.Selection(
        [
            ("scheduled", "Scheduled"),
            ("pending", "Awaiting confirmation"),
            ("confirmed", "Confirmed"),
            ("action_required", "Action required"),
        ],
        compute="_compute_daily_timestamp_proof",
        compute_sudo=True,
    )
    daily_timestamp_message = fields.Char(
        compute="_compute_daily_timestamp_proof",
        compute_sudo=True,
    )
    daily_timestamp_has_pending_receipt = fields.Boolean(
        compute="_compute_daily_timestamp_proof",
        compute_sudo=True,
    )
    daily_timestamp_has_confirmed_receipt = fields.Boolean(
        compute="_compute_daily_timestamp_proof",
        compute_sudo=True,
    )
    daily_timestamp_has_report = fields.Boolean(
        compute="_compute_daily_timestamp_proof",
        compute_sudo=True,
    )
    daily_timestamp_has_dossier = fields.Boolean(
        compute="_compute_daily_timestamp_proof",
        compute_sudo=True,
    )

    @api.depends(
        "state",
        "completed_at",
        "company_id.sign_opentimestamps_enabled",
        "daily_manifest_entry_ids.completion_event_hash",
        "daily_manifest_entry_ids.manifest_id.anchoring_status",
        "daily_manifest_entry_ids.manifest_id.bitcoin_block_time",
        "daily_manifest_entry_ids.manifest_id.archive_status",
        "daily_manifest_entry_ids.manifest_id.initial_receipt_id",
        "daily_manifest_entry_ids.manifest_id.confirmed_receipt_id",
        "daily_manifest_entry_ids.manifest_id.verification_report",
        "daily_manifest_entry_ids.manifest_id.proof_dossier",
    )
    def _compute_daily_timestamp_proof(self):
        for request in self:
            entries = request.daily_manifest_entry_ids.filtered(
                "completion_event_hash",
            ).sorted(
                lambda entry: (entry.manifest_id.manifest_date, entry.id),
                reverse=True,
            )
            manifest = entries[:1].manifest_id
            request.daily_timestamp_manifest_id = manifest
            request.daily_timestamp_has_pending_receipt = bool(
                manifest.initial_receipt_id,
            )
            request.daily_timestamp_has_confirmed_receipt = bool(
                manifest.confirmed_receipt_id,
            )
            request.daily_timestamp_has_report = bool(manifest.verification_report)
            request.daily_timestamp_has_dossier = bool(manifest.proof_dossier)
            if manifest:
                status = manifest.anchoring_status
                request.daily_timestamp_status = status
                if status == "confirmed":
                    message = _(
                        "Confirmed — existed no later than %(time)s",
                        time=fields.Datetime.to_string(manifest.bitcoin_block_time),
                    )
                    if manifest.archive_status == "failed":
                        message = _("%(proof)s · Daily proof archive needs attention", proof=message)
                    request.daily_timestamp_message = message
                elif status == "pending":
                    request.daily_timestamp_message = _("Awaiting Bitcoin confirmation")
                elif status == "action_required":
                    request.daily_timestamp_message = _("Timestamp proof requires review")
                else:
                    request.daily_timestamp_message = _("Scheduled for daily proof")
            elif request.state == "completed" and request.company_id.sign_opentimestamps_enabled:
                request.daily_timestamp_status = "scheduled"
                request.daily_timestamp_message = _("Scheduled for daily proof")
            else:
                request.daily_timestamp_status = False
                request.daily_timestamp_message = False

    def action_open_daily_timestamp_proof(self):
        self.ensure_one()
        if not self.env.user.has_group("usl_sign.group_sign_evidence_reviewer"):
            msg = "Evidence reviewer access is required."
            raise AccessError(msg)
        if not self.daily_timestamp_manifest_id:
            msg = "The completed request has not reached its daily proof yet."
            raise UserError(msg)
        return {
            "type": "ir.actions.act_window",
            "res_model": "usl.sign.daily.manifest",
            "res_id": self.daily_timestamp_manifest_id.id,
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "current",
        }

    def _daily_timestamp_download(self, record, field_name, filename):
        self.ensure_one()
        if not self.env.user.has_group("usl_sign.group_sign_evidence_reviewer"):
            msg = "Evidence reviewer access is required."
            raise AccessError(msg)
        if not record or not record[field_name] or not filename:
            msg = "This daily proof file is not available yet."
            raise UserError(msg)
        record.check_access("read")
        return {
            "type": "ir.actions.act_url",
            "url": (
                f"/web/content/{record._name}/{record.id}/{field_name}/"
                f"{quote(filename)}?download=true"
            ),
            "target": "self",
        }

    def action_download_daily_signed_manifest(self):
        self.ensure_one()
        manifest = self.daily_timestamp_manifest_id
        return self._daily_timestamp_download(
            manifest,
            "signed_envelope",
            manifest.signed_envelope_filename,
        )

    def action_download_daily_pending_receipt(self):
        self.ensure_one()
        receipt = self.daily_timestamp_manifest_id.initial_receipt_id
        return self._daily_timestamp_download(receipt, "data", receipt.name)

    def action_download_daily_confirmed_receipt(self):
        self.ensure_one()
        receipt = self.daily_timestamp_manifest_id.confirmed_receipt_id
        return self._daily_timestamp_download(receipt, "data", receipt.name)

    def action_download_daily_verification_report(self):
        self.ensure_one()
        manifest = self.daily_timestamp_manifest_id
        return self._daily_timestamp_download(
            manifest,
            "verification_report",
            manifest.verification_report_filename,
        )

    def action_download_daily_proof_dossier(self):
        self.ensure_one()
        manifest = self.daily_timestamp_manifest_id
        return self._daily_timestamp_download(
            manifest,
            "proof_dossier",
            manifest.proof_dossier_filename,
        )


class SignDocumentLink(models.Model):
    _inherit = "usl.document.link"

    @api.model
    def _allowed_models(self):
        return super()._allowed_models() | {"usl.sign.daily.manifest"}
