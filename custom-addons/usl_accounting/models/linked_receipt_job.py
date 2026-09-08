"""Isolated receipt fetch job: fetcher transport, guarded job state persistence and terminal failures."""

import base64
import hashlib
import json
import os
import ssl

import httpx

from odoo import _, fields, models

from .linked_receipt import (
    _LINKED_RECEIPT_INTERNAL,
    FETCH_FAILURE_CODES,
    MAX_PDF_BYTES,
    POSITIVE_TOKENS,
    ReceiptFetchError,
    _normalized_host,
    _safe_fetch_failure_message,
    _safe_filename,
    _safe_redirect_evidence,
    _tokens,
)
from odoo.addons.queue_job.exception import RetryableJobError


class UslMailPdfRetrieval(models.Model):
    _inherit = "usl.mail.pdf.retrieval"

    def _recover_url(self):
        candidate = self._candidate_by_fingerprint(self.selected_fingerprint)
        if not candidate:
            raise ReceiptFetchError(
                "source_link_missing",
                _("The selected link is no longer present in the source email."),
            )
        return candidate["_url"], candidate

    def _fetcher_request(self, url, candidate):
        endpoint = os.getenv("USL_RECEIPT_FETCHER_URL", "https://usl-receipt-fetcher").rstrip("/")
        socket_path = os.getenv(
            "USL_RECEIPT_FETCHER_SOCKET",
            "/run/receipt-control/fetcher.sock",
        )
        cert_dir = os.getenv("USL_RECEIPT_FETCHER_CERT_DIR", "/run/secrets/receipt-fetcher")
        verify = os.path.join(cert_dir, "ca.crt")
        try:
            tls_context = ssl.create_default_context(cafile=verify)
            tls_context.load_cert_chain(
                os.path.join(cert_dir, "odoo.crt"),
                os.path.join(cert_dir, "odoo.key"),
            )
            transport = httpx.HTTPTransport(
                verify=tls_context,
                uds=socket_path,
                retries=0,
            )
            with httpx.Client(
                transport=transport,
                timeout=httpx.Timeout(40.0, connect=5.0),
                trust_env=False,
            ) as client:
                with client.stream(
                    "POST",
                    f"{endpoint}/v1/receipts/fetch",
                    json={
                        "url": url,
                        "blocked_hosts": self.env["usl.mail.pdf.host"]
                        .sudo()
                        .search([("state", "=", "blocked")])
                        .mapped("hostname"),
                        "candidate": {
                            "label_tokens": candidate["label_tokens"],
                            "learned_action": self.pattern_id.learned_action or None,
                        },
                        "limits": {
                            "max_bytes": MAX_PDF_BYTES,
                            "max_redirects": 10,
                            "max_browser_requests": 75,
                            "deadline_seconds": 35,
                        },
                    },
                ) as response:
                    if response.status_code != 200:
                        try:
                            error_body = b""
                            for chunk in response.iter_bytes(4096):
                                error_body += chunk
                                if len(error_body) > 64 * 1024:
                                    error_body = b""
                                    break
                            payload = json.loads(error_body) if error_body else {}
                        except (UnicodeDecodeError, ValueError):
                            payload = {}
                        raw_code = str(payload.get("code") or "fetch_failed")
                        code = raw_code if raw_code in FETCH_FAILURE_CODES else "fetch_failed"
                        message = self.env._(_safe_fetch_failure_message(code))
                        retryable = response.status_code in (
                            408,
                            425,
                            429,
                            502,
                            503,
                            504,
                        ) or response.status_code >= 500
                        raise ReceiptFetchError(code, message, retryable=retryable)
                    chunks = []
                    size = 0
                    for chunk in response.iter_bytes(64 * 1024):
                        size += len(chunk)
                        if size > MAX_PDF_BYTES:
                            raise ReceiptFetchError(
                                "pdf_too_large",
                                _("The linked receipt is larger than 20 MB."),
                            )
                        chunks.append(chunk)
                    content = b"".join(chunks)
                    response_headers = dict(response.headers)
        except (httpx.RequestError, OSError):
            raise ReceiptFetchError(
                "fetcher_unavailable",
                _("The receipt download service is unavailable."),
                retryable=True,
            ) from None
        if not content.startswith(b"%PDF-"):
            raise ReceiptFetchError("invalid_pdf", _("The linked file is not a valid PDF receipt."))
        digest = hashlib.sha256(content).hexdigest()
        if response_headers.get("x-usl-sha256") not in (None, "", digest):
            raise ReceiptFetchError("checksum_mismatch", _("The downloaded receipt failed its integrity check."))
        filename = _safe_filename(response_headers.get("x-usl-filename") or "receipt.pdf")
        metadata = {
            "fetch_mode": (
                "browser"
                if response_headers.get("x-usl-fetch-mode") == "browser"
                else "http"
            ),
            "redirect_hosts": _safe_redirect_evidence(
                response_headers.get("x-usl-redirect-hosts"),
            ),
        }
        learned_action = response_headers.get("x-usl-learned-action")
        if learned_action:
            try:
                decoded = json.loads(
                    base64.urlsafe_b64decode(learned_action + "===").decode(),
                )
                if isinstance(decoded, dict) and decoded.get("role") == "control":
                    metadata["learned_action"] = {
                        "role": "control",
                        "tokens": " ".join(
                            sorted(
                                set(_tokens(str(decoded.get("tokens") or "")))
                                & set(POSITIVE_TOKENS),
                            ),
                        )[:120],
                    }
            except (ValueError, TypeError, json.JSONDecodeError):
                pass
        return content, filename, digest, metadata

    def _register_terminal_failure(self, error):
        self.sudo().write(
            {
                "state": "needs_attention",
                "failure_code": error.code,
                "failure_message": error.message,
            },
        )
        if self.pattern_id:
            self.pattern_id._register_terminal_failure(error.code)
        host = self.env["usl.mail.pdf.host"].sudo().search([("hostname", "=", self.starting_host)], limit=1)
        if host:
            host._locked()
            host.with_context(linked_receipt_internal=_LINKED_RECEIPT_INTERNAL).write(
                {"failure_count": host.failure_count + 1},
            )

    def _job_generation(self):
        """Recover the enqueue generation without adding it to job arguments."""
        job_uuid = self.env.context.get("job_uuid")
        if not job_uuid:
            return self.generation
        job = self.env["queue.job"].sudo().search([("uuid", "=", job_uuid)], limit=1)
        prefix = f"receipt-fetch:{self.id}:"
        if not job.identity_key or not job.identity_key.startswith(prefix):
            return -1
        try:
            return int(job.identity_key.removeprefix(prefix))
        except ValueError:
            return -1

    def _job_attempt_number(self):
        job_uuid = self.env.context.get("job_uuid")
        if not job_uuid:
            return self.attempt_count + 1
        job = self.env["queue.job"].sudo().search([("uuid", "=", job_uuid)], limit=1)
        return job.retry + 1 if job else self.attempt_count + 1

    def _persist_job_state(self, values, *, generation, allowed_states):
        """Persist a guarded job transition before queue_job rolls back.

        Receipt jobs opt into queue_job's temporary, commit-capable cursor.  A
        rollback here starts a fresh snapshot after a network call; locking the
        row then makes a concurrent dismissal, manual upload, or newer
        generation authoritative.
        """
        self.ensure_one()
        if not self.env.context.get("job_uuid"):
            self.sudo().write(values)
            return True
        self.env.cr.rollback()
        retrieval = self.sudo().exists()
        if not retrieval:
            return False
        self.env.cr.execute(
            "SELECT id FROM usl_mail_pdf_retrieval WHERE id = %s FOR UPDATE",
            (retrieval.id,),
        )
        retrieval.invalidate_recordset()
        if (
            retrieval.generation != generation
            or retrieval.state not in allowed_states
        ):
            self.env.cr.rollback()
            return False
        retrieval.write(values)
        self.env.cr.commit()
        retrieval.invalidate_recordset()
        return True

    def _persist_terminal_failure(self, error, *, generation):
        """Record one terminal outcome without overwriting newer user action."""
        self.ensure_one()
        if not self.env.context.get("job_uuid"):
            self._register_terminal_failure(error)
            return True
        self.env.cr.rollback()
        retrieval = self.sudo().exists()
        if not retrieval:
            return False
        self.env.cr.execute(
            "SELECT id FROM usl_mail_pdf_retrieval WHERE id = %s FOR UPDATE",
            (retrieval.id,),
        )
        retrieval.invalidate_recordset()
        if retrieval.generation != generation or retrieval.state != "running":
            self.env.cr.rollback()
            return False
        retrieval._register_terminal_failure(error)
        self.env.cr.commit()
        retrieval.invalidate_recordset()
        return True

    def _job_fetch_receipt_failed(self, **_failure_values):
        """Translate a terminal technical job failure into a safe domain state."""
        for retrieval in self.sudo().exists():
            if (
                retrieval.state == "retrying"
                and retrieval.running_generation == retrieval.generation
            ):
                retrieval._register_terminal_failure(
                    ReceiptFetchError(
                        retrieval.failure_code or "fetch_failed",
                        retrieval.failure_message
                        or _("The receipt download failed after four attempts."),
                    ),
                )

    def _job_fetch_receipt(self):
        self.ensure_one()
        generation = self._job_generation()
        retrieval = self.sudo().exists()
        if not retrieval or retrieval.generation != generation or retrieval.state in ("dismissed", "superseded", "succeeded"):
            return
        if not retrieval._feature_enabled():
            retrieval.write(
                {
                    "state": "needs_attention",
                    "failure_code": "feature_disabled",
                    "failure_message": _(
                        "Automatic linked-receipt download is disabled in this environment.",
                    ),
                },
            )
            return
        if not retrieval._expense_is_eligible(retrieval.expense_id):
            retrieval.write(
                {
                    "state": "superseded",
                    "generation": retrieval.generation + 1,
                    "failure_code": False,
                    "failure_message": False,
                },
            )
            return
        if retrieval.pattern_id.state in ("paused", "blocked"):
            retrieval.write(
                {
                    "state": "needs_attention",
                    "failure_code": "pattern_unavailable",
                    "failure_message": _(
                        "The learned receipt pattern is paused or blocked.",
                    ),
                },
            )
            return
        if self.env["usl.mail.pdf.host"].sudo().search_count(
            [("hostname", "=", retrieval.starting_host), ("state", "=", "blocked")],
        ):
            retrieval.write(
                {
                    "state": "needs_attention",
                    "failure_code": "egress_denied",
                    "failure_message": _(
                        "The receipt host is blocked for the Odoo instance.",
                    ),
                },
            )
            return
        if retrieval._has_manual_receipt():
            retrieval.write({"state": "superseded", "generation": retrieval.generation + 1})
            return
        if retrieval.pattern_id and not retrieval.pattern_id._should_probe_provider():
            # This provider has already refused unattended downloads for this
            # format.  Offer the employee handoff at once rather than spending
            # another request that can only be throttled and refused again.
            retrieval.write(
                {
                    "state": "needs_attention",
                    "failure_code": "authentication_required",
                    "failure_message": self.env._(
                        _safe_fetch_failure_message("authentication_required"),
                    ),
                },
            )
            return
        attempt_number = retrieval._job_attempt_number()
        running_values = {
            "state": "running",
            "attempt_count": attempt_number,
            "running_generation": generation,
            "last_attempt_at": fields.Datetime.now(),
            "failure_code": False,
            "failure_message": False,
        }
        if self.env.context.get("job_uuid"):
            if not retrieval._persist_job_state(
                running_values,
                generation=generation,
                # A worker can die or hit a serialization retry after the
                # durable running marker commits. OCA then requeues the same
                # job generation, which must be able to resume that marker.
                allowed_states=("queued", "running", "retrying"),
            ):
                return
        else:
            # Direct invocation is used by focused Odoo tests and does not run
            # in queue_job's commit-capable temporary cursor.
            retrieval.write(running_values)
        try:
            url, candidate = retrieval._recover_url()
            content, filename, digest, metadata = retrieval._fetcher_request(url, candidate)
        except ReceiptFetchError as error:
            if error.retryable and attempt_number <= 4:
                if not retrieval._persist_job_state(
                    {
                        "state": "retrying",
                        "attempt_count": attempt_number,
                        "running_generation": generation,
                        "failure_code": error.code,
                        "failure_message": error.message,
                    },
                    generation=generation,
                    allowed_states=("running",),
                ):
                    return
                raise RetryableJobError(error.message) from None
            retrieval._persist_terminal_failure(error, generation=generation)
            return
        except Exception:
            # Do not expose an exception string: parser and transport failures
            # can contain the signed URL. Retry with a fixed safe message and
            # let queue_job surface the bounded terminal failure.
            message = _("The receipt could not be downloaded.")
            if not retrieval._persist_job_state(
                {
                    "state": "retrying",
                    "attempt_count": attempt_number,
                    "running_generation": generation,
                    "failure_code": "fetch_failed",
                    "failure_message": message,
                },
                generation=generation,
                allowed_states=("running",),
            ):
                return
            raise RetryableJobError(message) from None
        # Fetching is deliberately lock-free. Serialize the short attachment
        # phase per expense so distinct source emails cannot both attach.
        if self.env.context.get("job_uuid"):
            # Refresh the REPEATABLE READ snapshot after the network call so a
            # user action committed while fetching cannot be overwritten.
            self.env.cr.rollback()
            retrieval = self.sudo().exists()
            if not retrieval:
                return
        self.env.cr.execute(
            "SELECT id FROM hr_expense WHERE id = %s FOR UPDATE",
            (retrieval.expense_id.id,),
        )
        if not self.env.cr.fetchone():
            return
        self.env.cr.execute(
            "SELECT id FROM usl_mail_pdf_retrieval WHERE id = %s FOR UPDATE",
            (retrieval.id,),
        )
        if not self.env.cr.fetchone():
            return
        retrieval.invalidate_recordset()
        retrieval.expense_id.invalidate_recordset()
        if (
            retrieval.generation != generation
            or retrieval.state in ("dismissed", "superseded", "succeeded")
            or not retrieval._expense_is_eligible(retrieval.expense_id)
        ):
            if retrieval.state not in ("dismissed", "superseded", "succeeded"):
                retrieval.write(
                    {
                        "state": "superseded",
                        "generation": retrieval.generation + 1,
                    },
                )
            return
        # Serialize global governance with the final attachment decision.  A
        # manager block that commits first wins; a block waiting on these rows
        # takes effect immediately after this already-validated completion.
        if retrieval.pattern_id:
            retrieval.pattern_id._locked()
            if retrieval.pattern_id.state in ("paused", "blocked"):
                retrieval.write(
                    {
                        "state": "needs_attention",
                        "failure_code": "pattern_unavailable",
                        "failure_message": _(
                            "The learned receipt pattern is paused or blocked.",
                        ),
                    },
                )
                return
        try:
            fetched_chain = json.loads(metadata.get("redirect_hosts") or "[]")
        except (TypeError, ValueError):
            fetched_chain = []
        fetched_hosts = {retrieval.starting_host}
        fetched_hosts.update(
            _normalized_host(item.get("host"))
            for item in fetched_chain
            if isinstance(item, dict)
        )
        Host = self.env["usl.mail.pdf.host"].sudo()
        chain_hosts = Host.search([("hostname", "in", list(fetched_hosts - {""}))])
        if chain_hosts:
            self.env.cr.execute(
                "SELECT id FROM usl_mail_pdf_host WHERE id = ANY(%s) FOR UPDATE",
                (chain_hosts.ids,),
            )
            chain_hosts.invalidate_recordset()
        if chain_hosts.filtered(lambda host: host.state == "blocked"):
            retrieval._register_terminal_failure(
                ReceiptFetchError(
                    "egress_denied",
                    _("A host in the receipt download chain was blocked."),
                ),
            )
            return
        if retrieval._has_manual_receipt():
            retrieval.write({"state": "superseded", "generation": retrieval.generation + 1})
            return
        duplicate = retrieval.search(
            [("expense_id", "=", retrieval.expense_id.id), ("sha256", "=", digest), ("state", "=", "succeeded")],
            limit=1,
        ).attachment_id
        attachment = duplicate or self.env["ir.attachment"].sudo().create(
            {
                "name": filename,
                "raw": content,
                "mimetype": "application/pdf",
                "res_model": "hr.expense",
                "res_id": retrieval.expense_id.id,
                "company_id": retrieval.company_id.id,
            },
        )
        if not duplicate:
            retrieval.expense_id.with_context(
                mail_post_autofollow_author_skip=True,
                linked_receipt_attachment=True,
            ).message_post(
                body=_("The linked PDF receipt was downloaded safely."),
                attachment_ids=[attachment.id],
                subtype_xmlid="mail.mt_note",
            )
            retrieval.expense_id.sudo()._message_set_main_attachment_id(
                attachment,
                force=True,
            )
        retrieval.write(
            {
                "state": "succeeded",
                "sha256": digest,
                "attachment_id": attachment.id,
                "fetch_mode": metadata.get("fetch_mode"),
                "redirect_hosts": metadata.get("redirect_hosts"),
                "failure_code": False,
                "failure_message": False,
            },
        )
        if retrieval.pattern_id:
            retrieval.pattern_id._register_success(metadata)
        chain = fetched_chain
        successful_hosts = {retrieval.starting_host}
        successful_hosts.update(
            _normalized_host(item.get("host"))
            for item in chain
            if isinstance(item, dict)
        )
        now = fields.Datetime.now()
        for hostname in sorted(successful_hosts - {""}):
            host = Host._get_or_create(hostname, state="active")
            host._locked()
            if host.state == "blocked":
                continue
            host.with_context(linked_receipt_internal=_LINKED_RECEIPT_INTERNAL).write(
                {
                    "state": "active",
                    "validated_pattern_id": retrieval.pattern_id.id or False,
                    "first_success_at": host.first_success_at or now,
                    "last_success_at": now,
                    "success_count": host.success_count + 1,
                },
            )
