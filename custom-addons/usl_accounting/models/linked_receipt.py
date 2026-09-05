import base64
import hashlib
import json
import os
import re
import ssl
from email.utils import parseaddr
from urllib.parse import parse_qsl, unquote, urlsplit

import httpx
from lxml import etree, html
from psycopg2 import IntegrityError

from odoo import _, api, fields, models
from odoo.addons.queue_job.exception import RetryableJobError
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import html2plaintext


MAX_CANDIDATES = 10
MAX_DISCOVERED_LINKS = 100
MAX_URL_LENGTH = 8192
MAX_PDF_BYTES = 20 * 1024 * 1024
AUTO_SCORE = 12
AUTO_MARGIN = 3
MIN_PATTERN_CONFIDENCE = 0.60
# RPC contexts are client-controlled; only in-process workflow code may edit
# learned evidence and governance state.
_LINKED_RECEIPT_INTERNAL = object()
FETCH_FAILURE_CODES = {
    "ambiguous_download",
    "authentication_required",
    "browser_crash",
    "browser_request_limit",
    "deadline",
    "egress_denied",
    "expired_or_forbidden",
    "fetch_failed",
    "form_submission_required",
    "http_error",
    "invalid_pdf",
    "no_pdf",
    "pdf_active_content",
    "pdf_encrypted",
    "pdf_too_large",
    "unsafe_url",
}

POSITIVE_TOKENS = {
    "download": 3,
    "invoice": 8,
    "pdf": 5,
    "receipt": 8,
    "recu": 8,
    "reçu": 8,
    "facture": 8,
    "justificatif": 8,
    "telecharger": 3,
    "télécharger": 3,
}
NEGATIVE_TOKENS = {
    "account",
    "auth",
    "facebook",
    "instagram",
    "login",
    "marketing",
    "password",
    "privacy",
    "signin",
    "social",
    "tracking",
    "unsubscribe",
}
SEMANTIC_TOKENS = set(POSITIVE_TOKENS) | NEGATIVE_TOKENS | {
    "bill",
    "billing",
    "click",
    "commande",
    "course",
    "document",
    "documents",
    "downloaded",
    "file",
    "files",
    "factures",
    "invoices",
    "order",
    "paiement",
    "payment",
    "ride",
    "rides",
    "receipts",
    "trip",
    "trips",
}
SAFE_PATH_SEGMENTS = SEMANTIC_TOKENS | {"api", "r", "v1", "v2", "v3"}
TOKEN_RE = re.compile(r"[^\W_]+", re.UNICODE)
PLAIN_URL_RE = re.compile(r"https://[^\s<>\"']+", re.IGNORECASE)
OPAQUE_SEGMENT_RE = re.compile(
    r"^(?:\d{3,}|[0-9a-f]{12,}|[0-9a-f]{8}-[0-9a-f-]{27,}|[A-Za-z0-9_-]{20,})$",
    re.IGNORECASE,
)
OPAQUE_TOKEN_RE = re.compile(r"(?:\d{6,}|[0-9a-f]{12,}|[A-Za-z0-9_-]{20,})", re.IGNORECASE)


class ReceiptFetchError(Exception):
    def __init__(self, code, message, *, retryable=False):
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


def _tokens(value):
    return sorted(
        {
            token
            for token in TOKEN_RE.findall((value or "").casefold())
            if not OPAQUE_TOKEN_RE.fullmatch(token)
        }
    )


def _normalized_host(value):
    try:
        return value.rstrip(".").encode("idna").decode("ascii").lower()
    except (UnicodeError, AttributeError):
        return ""


def _path_template(path):
    segments = []
    for raw_segment in (path or "/").split("/"):
        segment = unquote(raw_segment)
        normalized = segment.casefold()
        if not normalized:
            segments.append("")
        elif OPAQUE_SEGMENT_RE.fullmatch(segment) or OPAQUE_TOKEN_RE.search(segment):
            segments.append("{id}")
        elif normalized in SAFE_PATH_SEGMENTS:
            segments.append(normalized)
        elif normalized.endswith(".pdf"):
            stem = normalized[:-4]
            segments.append(f"{stem if stem in SAFE_PATH_SEGMENTS else '{id}'}.pdf")
        else:
            # Path segments commonly carry names, booking references, or other
            # personal data.  Retain only a small versioned semantic vocabulary.
            segments.append("{segment}")
    return "/".join(segments)[:512] or "/"


def _subject_skeleton(subject):
    skeleton = []
    for token in TOKEN_RE.findall((subject or "").casefold()):
        if any(character.isdigit() for character in token):
            value = "{id}"
        elif token in SEMANTIC_TOKENS:
            value = token
        else:
            continue
        if not skeleton or skeleton[-1] != value:
            skeleton.append(value)
    return " ".join(skeleton)[:256]


def _safe_label(label, host):
    label = re.sub(r"https?://\S+", "", label or "")
    label = OPAQUE_TOKEN_RE.sub("{id}", label)
    tokens = [
        token
        for token in TOKEN_RE.findall(label.casefold())
        if token in SEMANTIC_TOKENS
    ]
    return " ".join(dict.fromkeys(tokens))[:120] or host


def _safe_filename(value):
    # Provider filenames frequently contain passenger names, booking IDs, or
    # signed-link tokens.  The original name has no accounting meaning, so do
    # not duplicate any of it into Odoo's attachment or chatter metadata.
    return "receipt.pdf"


def _safe_fetch_failure_message(code):
    messages = {
        "ambiguous_download": "Several possible receipt downloads were found.",
        "authentication_required": "The receipt page requires authentication.",
        "browser_crash": "The isolated browser stopped unexpectedly.",
        "browser_request_limit": "The receipt page made too many network requests.",
        "deadline": "The receipt download took too long.",
        "egress_denied": "The network safety policy denied the destination.",
        "expired_or_forbidden": "The signed receipt link is expired or forbidden.",
        "fetch_failed": "The receipt could not be downloaded.",
        "form_submission_required": "The receipt page requires a form submission.",
        "http_error": "The receipt provider returned an error.",
        "invalid_pdf": "The downloaded file is not a structurally valid PDF.",
        "no_pdf": "No unambiguous PDF receipt was found.",
        "pdf_active_content": "The PDF contains unsupported active content.",
        "pdf_encrypted": "The PDF is encrypted.",
        "pdf_too_large": "The PDF exceeds the 20 MB safety limit.",
        "unsafe_url": "The receipt link is not an allowed public HTTPS URL.",
    }
    return messages.get(code, messages["fetch_failed"])


def _safe_redirect_evidence(value):
    try:
        items = json.loads(value or "[]")
    except (TypeError, ValueError):
        return "[]"
    sanitized = []
    for item in items[:11] if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        hostname = _normalized_host(item.get("host"))
        path = item.get("path")
        if not hostname or not isinstance(path, str):
            continue
        sanitized.append({"host": hostname, "path": _path_template(path.split("?", 1)[0])})
    return json.dumps(sanitized, sort_keys=True, separators=(",", ":"))


class UslMailPdfHost(models.Model):
    _name = "usl.mail.pdf.host"
    _description = "Linked receipt host"
    _order = "hostname"

    hostname = fields.Char(required=True, index=True)
    state = fields.Selection(
        [("provisional", "Provisional"), ("active", "Active"), ("blocked", "Blocked")],
        required=True,
        default="provisional",
        index=True,
    )
    confirmed_by_id = fields.Many2one("res.users", readonly=True)
    confirmed_at = fields.Datetime(readonly=True)
    validated_pattern_id = fields.Many2one(
        "usl.mail.pdf.pattern",
        readonly=True,
        ondelete="set null",
    )
    first_success_at = fields.Datetime(readonly=True)
    last_success_at = fields.Datetime(readonly=True)
    success_count = fields.Integer(readonly=True)
    failure_count = fields.Integer(readonly=True)

    _hostname_unique = models.Constraint(
        "UNIQUE(hostname)",
        "A linked receipt host can only be registered once.",
    )

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            vals["hostname"] = _normalized_host(vals.get("hostname"))
            if not vals["hostname"]:
                raise ValidationError(_("Enter a valid host name."))
        return super().create(vals_list)

    def write(self, vals):
        if "hostname" in vals:
            raise UserError(_("A learned host name cannot be changed."))
        if self.env.context.get("linked_receipt_internal") is not _LINKED_RECEIPT_INTERNAL:
            raise AccessError(
                _("Use the linked-receipt governance actions to change a host.")
            )
        return super().write(vals)

    def action_block(self):
        self.check_access("write")
        self.with_context(linked_receipt_internal=_LINKED_RECEIPT_INTERNAL).write({"state": "blocked"})

    def action_activate(self):
        self.check_access("write")
        for host in self:
            host.with_context(linked_receipt_internal=_LINKED_RECEIPT_INTERNAL).write(
                {"state": "active" if host.success_count else "provisional"}
            )

    @api.model
    def _get_or_create(self, hostname, **create_values):
        """Return one normalized host under concurrent teaching/fetch jobs."""
        hostname = _normalized_host(hostname)
        host = self.sudo().search([("hostname", "=", hostname)], limit=1)
        if host:
            return host
        try:
            with self.env.cr.savepoint():
                return self.sudo().create({"hostname": hostname, **create_values})
        except IntegrityError:
            return self.sudo().search([("hostname", "=", hostname)], limit=1)

    def _locked(self):
        self.ensure_one()
        self.env.cr.execute(
            "SELECT id FROM usl_mail_pdf_host WHERE id = %s FOR UPDATE",
            (self.id,),
        )
        self.invalidate_recordset()
        return self


class UslMailPdfPattern(models.Model):
    _name = "usl.mail.pdf.pattern"
    _description = "Learned linked receipt pattern"
    _order = "last_used_at desc, id desc"

    signature = fields.Char(required=True, index=True, readonly=True)
    sender_domain = fields.Char(index=True, readonly=True)
    subject_skeleton = fields.Char(readonly=True)
    hostname = fields.Char(index=True, readonly=True)
    path_template = fields.Char(readonly=True)
    label_tokens = fields.Char(readonly=True)
    query_keys = fields.Char(readonly=True)
    observed_final_host = fields.Char(index=True, readonly=True)
    observed_final_path_template = fields.Char(readonly=True)
    preferred_fetch_mode = fields.Selection(
        [("http", "Direct HTTP"), ("browser", "Browser")],
        readonly=True,
    )
    learned_action = fields.Json(readonly=True)
    state = fields.Selection(
        [("learning", "Learning"), ("active", "Active"), ("paused", "Paused"), ("blocked", "Blocked")],
        required=True,
        default="learning",
        index=True,
    )
    positive_count = fields.Integer(readonly=True)
    negative_count = fields.Integer(readonly=True)
    success_count = fields.Integer(readonly=True)
    failure_count = fields.Integer(readonly=True)
    consecutive_failure_count = fields.Integer(readonly=True)
    confidence = fields.Float(compute="_compute_confidence", store=True, readonly=True)
    last_used_at = fields.Datetime(readonly=True)

    _signature_unique = models.Constraint(
        "UNIQUE(signature)",
        "A learned receipt pattern can only be registered once.",
    )

    def write(self, vals):
        if self.env.context.get("linked_receipt_internal") is not _LINKED_RECEIPT_INTERNAL:
            raise AccessError(
                _("Use the linked-receipt governance actions to change a pattern.")
            )
        return super().write(vals)

    @api.depends("positive_count", "negative_count", "success_count", "failure_count")
    def _compute_confidence(self):
        for pattern in self:
            positive = pattern.positive_count + pattern.success_count
            total = positive + pattern.negative_count + pattern.failure_count
            pattern.confidence = positive / total if total else 0.0

    @api.model
    def _learn(self, candidate, *, positive):
        pattern = self.sudo().search([("signature", "=", candidate["signature"])], limit=1)
        if not pattern:
            try:
                with self.env.cr.savepoint():
                    pattern = self.sudo().create(
                        {
                            "signature": candidate["signature"],
                            "sender_domain": candidate["sender_domain"],
                            "subject_skeleton": candidate["subject_skeleton"],
                            "hostname": candidate["hostname"],
                            "path_template": candidate["path_template"],
                            "label_tokens": " ".join(candidate["label_tokens"]),
                            "query_keys": " ".join(candidate["query_keys"]),
                        }
                    )
            except IntegrityError:
                pattern = self.sudo().search(
                    [("signature", "=", candidate["signature"])], limit=1
                )
        pattern._locked()
        if positive and pattern.state == "blocked":
            return pattern
        values = {"last_used_at": fields.Datetime.now()}
        counter = "positive_count" if positive else "negative_count"
        values[counter] = pattern[counter] + 1
        if positive and pattern.state == "paused":
            # A deliberate employee choice is new evidence, not another
            # automatic retry of the stale matcher.
            values.update({"state": "learning", "consecutive_failure_count": 0})
        pattern.sudo().with_context(linked_receipt_internal=_LINKED_RECEIPT_INTERNAL).write(values)
        return pattern

    def _locked(self):
        self.ensure_one()
        self.env.cr.execute(
            "SELECT id FROM usl_mail_pdf_pattern WHERE id = %s FOR UPDATE",
            (self.id,),
        )
        self.invalidate_recordset()
        return self

    def _register_success(self, metadata):
        self.ensure_one()
        self._locked()
        now = fields.Datetime.now()
        try:
            chain = json.loads(metadata.get("redirect_hosts") or "[]")
        except (TypeError, ValueError):
            chain = []
        final = chain[-1] if isinstance(chain, list) and chain else {}
        self.sudo().with_context(linked_receipt_internal=_LINKED_RECEIPT_INTERNAL).write(
            {
                "state": "active",
                "success_count": self.success_count + 1,
                "consecutive_failure_count": 0,
                "preferred_fetch_mode": metadata.get("fetch_mode") or False,
                "learned_action": metadata.get("learned_action") or False,
                "observed_final_host": _normalized_host(final.get("host")),
                "observed_final_path_template": (
                    _path_template(final.get("path")) if final else False
                ),
                "last_used_at": now,
            }
        )

    def _register_terminal_failure(self):
        self.ensure_one()
        self._locked()
        failures = self.consecutive_failure_count + 1
        self.sudo().with_context(linked_receipt_internal=_LINKED_RECEIPT_INTERNAL).write(
            {
                "failure_count": self.failure_count + 1,
                "consecutive_failure_count": failures,
                "state": "paused" if failures >= 2 else self.state,
                "last_used_at": fields.Datetime.now(),
            }
        )

    def action_pause(self):
        self.check_access("write")
        self.with_context(linked_receipt_internal=_LINKED_RECEIPT_INTERNAL).write({"state": "paused"})

    def action_activate(self):
        self.check_access("write")
        for pattern in self:
            pattern.with_context(linked_receipt_internal=_LINKED_RECEIPT_INTERNAL).write(
                {
                    "state": "active" if pattern.success_count else "learning",
                    "consecutive_failure_count": 0,
                }
            )

    def action_block(self):
        self.check_access("write")
        self.with_context(linked_receipt_internal=_LINKED_RECEIPT_INTERNAL).write({"state": "blocked"})


class UslMailPdfRetrieval(models.Model):
    _name = "usl.mail.pdf.retrieval"
    _description = "Linked expense receipt retrieval"
    _order = "create_date desc, id desc"
    _check_company_auto = True

    expense_id = fields.Many2one(
        "hr.expense",
        required=True,
        index=True,
        ondelete="cascade",
        check_company=True,
    )
    company_id = fields.Many2one(
        related="expense_id.company_id",
        store=True,
        index=True,
    )
    source_message_id = fields.Many2one(
        "mail.message",
        index=True,
        ondelete="set null",
    )
    candidate_features = fields.Json(readonly=True)
    selected_fingerprint = fields.Char(index=True, readonly=True)
    selected_signature = fields.Char(readonly=True)
    selected_label = fields.Char(readonly=True)
    starting_host = fields.Char(readonly=True)
    path_template = fields.Char(readonly=True)
    query_keys = fields.Char(readonly=True)
    pattern_id = fields.Many2one("usl.mail.pdf.pattern", readonly=True, ondelete="set null")
    state = fields.Selection(
        [
            ("selection_required", "Selection required"),
            ("queued", "Queued"),
            ("running", "Running"),
            ("retrying", "Retrying"),
            ("succeeded", "Succeeded"),
            ("needs_attention", "Needs attention"),
            ("superseded", "Superseded"),
            ("dismissed", "Dismissed"),
        ],
        required=True,
        default="selection_required",
        index=True,
        readonly=True,
    )
    generation = fields.Integer(default=1, required=True, readonly=True)
    running_generation = fields.Integer(readonly=True)
    attempt_count = fields.Integer(readonly=True)
    last_attempt_at = fields.Datetime(readonly=True)
    failure_code = fields.Char(readonly=True)
    failure_message = fields.Char(readonly=True)
    fetch_mode = fields.Selection(
        [("http", "Direct HTTP"), ("browser", "Browser")],
        readonly=True,
    )
    redirect_hosts = fields.Text(readonly=True)
    sha256 = fields.Char(index=True, readonly=True)
    attachment_id = fields.Many2one("ir.attachment", readonly=True, ondelete="set null")
    handoff_open_count = fields.Integer(readonly=True)
    last_handoff_at = fields.Datetime(readonly=True)
    last_handoff_user_id = fields.Many2one("res.users", readonly=True)

    _source_message_unique = models.Constraint(
        "UNIQUE(source_message_id)",
        "A source email can only create one linked receipt retrieval.",
    )

    @api.model
    def _feature_enabled(self):
        if os.getenv("USL_LINKED_PDF_DOWNLOAD_ENABLED", "0") != "1":
            return False
        if self.env["ir.config_parameter"].sudo().get_bool("database.is_neutralized"):
            return False
        deployment = os.getenv("USL_DEPLOYMENT_ENV", "development").strip().casefold()
        return deployment != "production" or os.getenv(
            "USL_LINKED_PDF_DOWNLOAD_ADMITTED", "0"
        ) == "1"

    @api.model
    def _extract_candidates(self, message, *, max_candidates=MAX_CANDIDATES):
        message = message.sudo().exists()
        if not message:
            return []
        body = str(message.body or "")
        sender = parseaddr(message.email_from or "")[1].casefold()
        sender_domain = _normalized_host(sender.rpartition("@")[2])
        subject = message.subject or ""
        subject_skeleton = _subject_skeleton(subject)
        discovered = []
        try:
            root = html.fragment_fromstring(body, create_parent="div")
            receipt_cta_seen = False
            for position, node in enumerate(root.iter("a")):
                if position >= MAX_DISCOVERED_LINKS:
                    break
                url = node.get("href")
                if not url:
                    continue
                label = " ".join(node.text_content().split())
                parent = node.getparent()
                parent_text = ""
                if parent is not None and len(parent.xpath(".//a")) <= 3:
                    # Keep nearby non-link copy, but never let the label of a
                    # sibling navigation link become this anchor's evidence.
                    parent_text = " ".join(
                        " ".join(value.split())
                        for value in parent.xpath(".//text()[not(ancestor::a)]")
                        if value.strip()
                    )
                # Image-only and CSS-styled CTAs are common in provider emails.
                # When the anchor and its immediate parent carry no positive
                # semantics, use only a bounded slice of preceding visible
                # text. The slice is never persisted; candidate snapshots keep
                # only the allowlisted semantic tokens derived from it.
                local_tokens = set(_tokens(f"{label} {parent_text}"))
                if (
                    not label
                    and not receipt_cta_seen
                    and not (local_tokens & set(POSITIVE_TOKENS))
                ):
                    preceding_text = " ".join(node.xpath("preceding::text()"))
                    parent_text = preceding_text[-1000:]
                class_tokens = set(re.split(r"\s+", (node.get("class") or "").casefold()))
                role = (
                    "button"
                    if (node.get("role") or "").casefold() == "button"
                    or class_tokens & {"btn", "button", "cta"}
                    else "link"
                )
                semantic_context = " ".join(
                    token for token in _tokens(parent_text) if token in SEMANTIC_TOKENS
                )[:240]
                receipt_cta_seen = receipt_cta_seen or bool(
                    set(_tokens(f"{label} {semantic_context}"))
                    & set(POSITIVE_TOKENS)
                )
                discovered.append((url, label, semantic_context, position, role))
        except (ValueError, TypeError, etree.ParserError, etree.XMLSyntaxError):
            pass
        plaintext = html2plaintext(body)
        for position, match in enumerate(PLAIN_URL_RE.finditer(plaintext), start=len(discovered)):
            if position >= MAX_DISCOVERED_LINKS:
                break
            discovered.append((match.group(0).rstrip(".,);]"), "", "", position, "text"))

        candidates = []
        seen = set()
        Pattern = self.env["usl.mail.pdf.pattern"].sudo()
        Host = self.env["usl.mail.pdf.host"].sudo()
        for url, label, context, position, role in discovered:
            if (
                not isinstance(url, str)
                or len(url) > MAX_URL_LENGTH
                or url in seen
                or "\\" in url
                or any(ord(character) < 32 for character in url)
            ):
                continue
            seen.add(url)
            try:
                parsed = urlsplit(url)
                port = parsed.port
            except ValueError:
                continue
            hostname = _normalized_host(parsed.hostname)
            if (
                parsed.scheme.casefold() != "https"
                or not hostname
                or parsed.username
                or parsed.password
                or port not in (None, 443)
            ):
                continue
            label = _safe_label(label, hostname)
            label_tokens = sorted(
                set(_tokens(context if label == hostname else f"{label} {context}"))
                & SEMANTIC_TOKENS
            )
            if label == hostname and label_tokens:
                label = " ".join(label_tokens)
            path_template = _path_template(parsed.path)
            query_keys = sorted(
                {
                    key.casefold()
                    for key, _value in parse_qsl(
                        parsed.query,
                        keep_blank_values=True,
                    )
                    if not OPAQUE_TOKEN_RE.search(key)
                }
            )[:20]
            signal_tokens = set(label_tokens) | set(_tokens(path_template))
            negative_tokens = signal_tokens & NEGATIVE_TOKENS
            positive_tokens = signal_tokens & set(POSITIVE_TOKENS)
            if negative_tokens and not (
                negative_tokens == {"tracking"} and positive_tokens
            ):
                continue
            score = sum(weight for token, weight in POSITIVE_TOKENS.items() if token in signal_tokens)
            if parsed.path.casefold().endswith(".pdf"):
                score += 4
            generic_pdf_signature = bool(
                parsed.path.casefold().endswith(".pdf")
                and positive_tokens
                & {"facture", "invoice", "justificatif", "receipt", "recu", "reçu"}
            )
            canonical = {
                "sender_domain": sender_domain,
                "subject_skeleton": subject_skeleton,
                "hostname": hostname,
                "path_template": path_template,
                "label_tokens": label_tokens,
                "query_keys": query_keys,
            }
            signature = hashlib.sha256(
                json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            matching = Pattern.search(
                [
                    ("sender_domain", "=", sender_domain),
                    ("hostname", "=", hostname),
                    ("state", "=", "active"),
                ]
            )
            compatible = matching.filtered(
                lambda item: item.confidence >= MIN_PATTERN_CONFIDENCE
                and (
                    item.signature == signature
                    or (
                        item.subject_skeleton == subject_skeleton
                        and (
                            item.path_template == path_template
                            or bool(set(item.label_tokens.split()) & set(label_tokens))
                        )
                    )
                )
            )
            exact = compatible.filtered(lambda item: item.signature == signature)
            pattern = (exact or compatible).sorted(
                key=lambda item: (item.confidence, item.success_count, item.id),
                reverse=True,
            )[:1]
            if pattern:
                score += 15 if pattern.path_template == path_template else 10
            if score <= 0:
                continue
            host = Host.search([("hostname", "=", hostname)], limit=1)
            candidates.append(
                {
                    **canonical,
                    "fingerprint": hashlib.sha256(url.encode()).hexdigest(),
                    "signature": signature,
                    "label": label,
                    "position": position,
                    "role": role,
                    "score": score,
                    "pattern_id": pattern.id if pattern else False,
                    "host_active": bool(host and host.state == "active"),
                    "generic_pdf_signature": generic_pdf_signature,
                    "_url": url,
                }
            )
        ranked = sorted(
            candidates,
            key=lambda item: (-item["score"], item["position"]),
        )
        limit = max(0, min(int(max_candidates), MAX_DISCOVERED_LINKS))
        return ranked[:limit]

    @api.model
    def _safe_candidate_snapshot(self, candidate):
        return {key: value for key, value in candidate.items() if key != "_url"}

    @api.model
    def _message_has_receipt(self, message):
        body = str(message.body or "")
        inline_ids = {
            int(value)
            for value in re.findall(r"/(?:web/image|web/content)/(\d+)", body)
        }
        return bool(
            message.attachment_ids.filtered(
                lambda attachment: attachment.id not in inline_ids
                and attachment.mimetype
                and (
                    attachment.mimetype == "application/pdf"
                    or attachment.mimetype.startswith("image/")
                )
            )
        )

    @api.model
    def _discover_for_expense(self, expense, message):
        if not self._feature_enabled() or not self._expense_is_eligible(expense):
            return self.browse()
        if self.sudo().search_count([("source_message_id", "=", message.id)]):
            return self.browse()
        if self._message_has_receipt(message):
            return self.browse()
        candidates = self._extract_candidates(message)
        if not candidates:
            return self.browse()
        top = candidates[0]
        runner_score = candidates[1]["score"] if len(candidates) > 1 else -999
        automatic = (
            top["host_active"]
            and (top["pattern_id"] or top["generic_pdf_signature"])
            and top["score"] >= AUTO_SCORE
            and top["score"] - runner_score >= AUTO_MARGIN
        )
        retrieval = self.sudo().create(
            {
                "expense_id": expense.id,
                "source_message_id": message.id,
                "candidate_features": [self._safe_candidate_snapshot(item) for item in candidates],
            }
        )
        if automatic:
            retrieval._select_candidate(top["fingerprint"], teach=False)
            retrieval._enqueue()
        return retrieval

    @api.model
    def _expense_is_eligible(self, expense):
        expense = expense.exists()
        return bool(expense and expense.state == "draft")

    def _check_can_manage(self):
        self.ensure_one()
        self.expense_id.check_access("read")
        own_expense = self.expense_id.employee_id.user_id == self.env.user
        manager = self.env.user.has_group("account.group_account_manager")
        if not own_expense and not manager:
            raise AccessError(_("Only the expense owner or an Accounting Manager can manage its linked receipt."))

    def _check_can_open_handoff(self, *, expected_generation=None):
        """Return the selected candidate when an employee may open it manually."""
        self.ensure_one()
        self.check_access("read")
        if self.expense_id.employee_id.user_id != self.env.user:
            raise AccessError(_("Only the expense owner can open its receipt website."))
        if not self._feature_enabled():
            raise UserError(_("Linked receipt retrieval is disabled in this environment."))
        if (
            self.state != "needs_attention"
            or self.failure_code != "authentication_required"
            or (
                expected_generation is not None
                and self.generation != expected_generation
            )
        ):
            raise UserError(_("This receipt recovery request is no longer active."))
        if not self._expense_is_eligible(self.expense_id):
            raise UserError(_("This expense can no longer receive a linked receipt."))
        if self._has_manual_receipt():
            raise UserError(_("A receipt has already been attached to this expense."))
        host = self.env["usl.mail.pdf.host"].sudo().search(
            [("hostname", "=", self.starting_host)],
            limit=1,
        )
        if host.state == "blocked" or self.pattern_id.sudo().state == "blocked":
            raise UserError(_("This receipt website is unavailable for the Odoo instance."))
        candidate = self._candidate_by_fingerprint(self.selected_fingerprint)
        if not candidate:
            raise UserError(_("The receipt link is no longer available in the source email."))
        if candidate["hostname"] != self.starting_host:
            raise UserError(_("The receipt link no longer matches this recovery request."))
        return candidate

    @api.private
    def _consume_handoff(self, *, expected_generation):
        """Lock, recheck, and consume one employee-controlled browser handoff."""
        self.ensure_one()
        self._check_can_open_handoff(expected_generation=expected_generation)
        self.env.cr.execute(
            "SELECT id FROM usl_mail_pdf_retrieval WHERE id = %s FOR UPDATE",
            (self.id,),
        )
        if not self.env.cr.fetchone():
            raise UserError(_("This receipt recovery request is no longer active."))
        self.invalidate_recordset()
        self.expense_id.invalidate_recordset()
        candidate = self._check_can_open_handoff(
            expected_generation=expected_generation,
        )
        self.sudo().write(
            {
                "handoff_open_count": self.handoff_open_count + 1,
                "last_handoff_at": fields.Datetime.now(),
                "last_handoff_user_id": self.env.user.id,
            }
        )
        return candidate["_url"]

    def _candidate_by_fingerprint(self, fingerprint):
        self.ensure_one()
        return next(
            (
                candidate
                for candidate in self._extract_candidates(
                    self.source_message_id,
                    max_candidates=MAX_DISCOVERED_LINKS,
                )
                if candidate["fingerprint"] == fingerprint
            ),
            None,
        )

    def _select_candidate(self, fingerprint, *, teach):
        self.ensure_one()
        candidate = self._candidate_by_fingerprint(fingerprint)
        if not candidate:
            raise UserError(_("The selected receipt link is no longer present in the source email."))
        Host = self.env["usl.mail.pdf.host"].sudo()
        host = Host.search([("hostname", "=", candidate["hostname"])], limit=1)
        if host and host.state == "blocked":
            raise UserError(_("This receipt host is blocked for the Odoo instance."))
        Pattern = self.env["usl.mail.pdf.pattern"].sudo()
        pattern = (
            Pattern._learn(candidate, positive=True)
            if teach
            else Pattern.browse(candidate["pattern_id"])
        )
        if pattern.state == "blocked":
            raise UserError(
                _("This receipt pattern is blocked for the Odoo instance.")
            )
        if teach:
            for rejected in self._extract_candidates(self.source_message_id):
                if rejected["fingerprint"] != fingerprint:
                    Pattern._learn(rejected, positive=False)
        if not host:
            host = Host._get_or_create(
                candidate["hostname"],
                confirmed_by_id=self.env.user.id,
                confirmed_at=fields.Datetime.now(),
            )
        if host.state == "blocked":
            raise UserError(_("This receipt host is blocked for the Odoo instance."))
        self.sudo().write(
            {
                "selected_fingerprint": candidate["fingerprint"],
                "selected_signature": candidate["signature"],
                "selected_label": candidate["label"],
                "starting_host": candidate["hostname"],
                "path_template": candidate["path_template"],
                "query_keys": " ".join(candidate["query_keys"]),
                "pattern_id": pattern.id,
                "failure_code": False,
                "failure_message": False,
            }
        )
        return candidate

    def action_select_candidate(self, fingerprint):
        self.ensure_one()
        self._check_can_manage()
        if self.state not in ("selection_required", "needs_attention"):
            raise UserError(_("This linked receipt no longer needs a link selection."))
        self._select_candidate(fingerprint, teach=True)
        self._enqueue()
        return True

    def _enqueue(self):
        self.ensure_one()
        if not self._feature_enabled():
            self.sudo().write(
                {
                    "state": "needs_attention",
                    "failure_code": "feature_disabled",
                    "failure_message": _("Automatic linked-receipt download is disabled in this environment."),
                }
            )
            return
        self.sudo().write({"state": "queued"})
        self.with_delay(
            channel="root.receipt_fetch",
            max_retries=4,
            description=_("Fetch linked receipt %(retrieval)s", retrieval=self.id),
            identity_key=f"receipt-fetch:{self.id}:{self.generation}",
        )._job_fetch_receipt()

    def action_retry(self):
        self.ensure_one()
        self._check_can_manage()
        if self.state != "needs_attention":
            raise UserError(_("Only a linked receipt needing attention can be retried."))
        if not self.selected_fingerprint:
            raise UserError(_("Choose a receipt link before retrying."))
        pattern = self.pattern_id.sudo()
        if pattern.state == "blocked":
            raise UserError(
                _("This receipt pattern is blocked for the Odoo instance.")
            )
        host = self.env["usl.mail.pdf.host"].sudo().search(
            [("hostname", "=", self.starting_host)], limit=1
        )
        if host.state == "blocked":
            raise UserError(_("This receipt host is blocked for the Odoo instance."))
        if pattern.state == "paused":
            pattern.with_context(linked_receipt_internal=_LINKED_RECEIPT_INTERNAL).write(
                {"state": "learning", "consecutive_failure_count": 0}
            )
        self.sudo().write(
            {
                "generation": self.generation + 1,
                "attempt_count": 0,
                "failure_code": False,
                "failure_message": False,
            }
        )
        self._enqueue()
        return True

    def action_dismiss(self):
        self.ensure_one()
        self._check_can_manage()
        if self.state not in (
            "selection_required",
            "queued",
            "running",
            "retrying",
            "needs_attention",
        ):
            raise UserError(_("This linked receipt is already complete."))
        self.sudo().write({"state": "dismissed", "generation": self.generation + 1})
        return True

    def _has_manual_receipt(self):
        self.ensure_one()
        if (
            self.expense_id.message_main_attachment_id
            and self.expense_id.message_main_attachment_id != self.attachment_id
        ):
            return True
        messages = self.expense_id.message_ids.filtered(
            lambda item: item.id != self.source_message_id.id
        )
        return any(self._message_has_receipt(message) for message in messages)

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
                response_headers.get("x-usl-redirect-hosts")
            ),
        }
        learned_action = response_headers.get("x-usl-learned-action")
        if learned_action:
            try:
                decoded = json.loads(
                    base64.urlsafe_b64decode(learned_action + "===").decode()
                )
                if isinstance(decoded, dict) and decoded.get("role") == "control":
                    metadata["learned_action"] = {
                        "role": "control",
                        "tokens": " ".join(
                            sorted(
                                set(_tokens(str(decoded.get("tokens") or "")))
                                & set(POSITIVE_TOKENS)
                            )
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
            }
        )
        if self.pattern_id:
            self.pattern_id._register_terminal_failure()
        host = self.env["usl.mail.pdf.host"].sudo().search([("hostname", "=", self.starting_host)], limit=1)
        if host:
            host._locked()
            host.with_context(linked_receipt_internal=_LINKED_RECEIPT_INTERNAL).write(
                {"failure_count": host.failure_count + 1}
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
                    )
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
                        "Automatic linked-receipt download is disabled in this environment."
                    ),
                }
            )
            return
        if not retrieval._expense_is_eligible(retrieval.expense_id):
            retrieval.write(
                {
                    "state": "superseded",
                    "generation": retrieval.generation + 1,
                    "failure_code": False,
                    "failure_message": False,
                }
            )
            return
        if retrieval.pattern_id.state in ("paused", "blocked"):
            retrieval.write(
                {
                    "state": "needs_attention",
                    "failure_code": "pattern_unavailable",
                    "failure_message": _(
                        "The learned receipt pattern is paused or blocked."
                    ),
                }
            )
            return
        if self.env["usl.mail.pdf.host"].sudo().search_count(
            [("hostname", "=", retrieval.starting_host), ("state", "=", "blocked")]
        ):
            retrieval.write(
                {
                    "state": "needs_attention",
                    "failure_code": "egress_denied",
                    "failure_message": _(
                        "The receipt host is blocked for the Odoo instance."
                    ),
                }
            )
            return
        if retrieval._has_manual_receipt():
            retrieval.write({"state": "superseded", "generation": retrieval.generation + 1})
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
                    }
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
                            "The learned receipt pattern is paused or blocked."
                        ),
                    }
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
                )
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
            }
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
            }
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
                }
            )

    @api.model
    def _supersede_for_expense(self, expense):
        retrievals = self.sudo().search(
            [
                ("expense_id", "=", expense.id),
                ("state", "in", ("selection_required", "queued", "running", "retrying", "needs_attention")),
            ]
        )
        for retrieval in retrievals:
            retrieval.write(
                {
                    "state": "superseded",
                    "generation": retrieval.generation + 1,
                }
            )


class HrExpense(models.Model):
    _inherit = "hr.expense"

    linked_receipt_state = fields.Selection(
        selection=[
            ("selection_required", "Selection required"),
            ("queued", "Queued"),
            ("running", "Running"),
            ("retrying", "Retrying"),
            ("succeeded", "Succeeded"),
            ("needs_attention", "Needs attention"),
            ("superseded", "Superseded"),
            ("dismissed", "Dismissed"),
        ],
        compute="_compute_linked_receipt_status",
        compute_sudo=True,
    )
    linked_receipt_message = fields.Char(compute="_compute_linked_receipt_status", compute_sudo=True)
    linked_receipt_can_manage = fields.Boolean(compute="_compute_linked_receipt_can_manage")
    linked_receipt_can_open_website = fields.Boolean(
        compute="_compute_linked_receipt_can_manage"
    )
    linked_receipt_authentication_required = fields.Boolean(
        compute="_compute_linked_receipt_status",
        compute_sudo=True,
    )

    def _compute_linked_receipt_status(self):
        Retrieval = self.env["usl.mail.pdf.retrieval"].sudo()
        for expense in self:
            retrieval = Retrieval.search([("expense_id", "=", expense.id)], order="id desc", limit=1)
            expense.linked_receipt_state = retrieval.state or False
            expense.linked_receipt_authentication_required = bool(
                retrieval.state == "needs_attention"
                and retrieval.failure_code == "authentication_required"
            )
            if not retrieval:
                expense.linked_receipt_message = False
            elif retrieval.state == "selection_required":
                expense.linked_receipt_message = _("Choose the receipt link so Odoo can learn this email format.")
            elif retrieval.state in ("queued", "running"):
                expense.linked_receipt_message = _("Odoo is downloading the linked PDF receipt.")
            elif retrieval.state == "retrying":
                expense.linked_receipt_message = _("The receipt download will retry automatically.")
            elif retrieval.state == "needs_attention":
                if retrieval.failure_code == "authentication_required":
                    expense.linked_receipt_message = _(
                        "Sign in on the receipt website, download the PDF, then attach it here. Your credentials stay with the provider."
                    )
                else:
                    expense.linked_receipt_message = retrieval.failure_message or _("The linked receipt needs attention.")
            else:
                expense.linked_receipt_message = False

    def _compute_linked_receipt_can_manage(self):
        is_manager = self.env.user.has_group("account.group_account_manager")
        for expense in self:
            expense.linked_receipt_can_manage = bool(
                expense.employee_id.user_id == self.env.user or is_manager
            )
            expense.linked_receipt_can_open_website = bool(
                expense.employee_id.user_id == self.env.user
            )

    def _latest_linked_receipt(self):
        self.ensure_one()
        retrieval = self.env["usl.mail.pdf.retrieval"].sudo().search(
            [("expense_id", "=", self.id)], order="id desc", limit=1
        )
        if not retrieval:
            raise UserError(_("This expense has no linked receipt to manage."))
        return retrieval

    def action_review_linked_receipt(self):
        self.ensure_one()
        retrieval = self._latest_linked_receipt()
        retrieval.with_user(self.env.user)._check_can_manage()
        Wizard = self.env["usl.mail.pdf.candidate.wizard"]
        wizard = Wizard.create(
            {
                "retrieval_id": retrieval.id,
                "candidate_ids": Wizard._candidate_commands(retrieval),
            }
        )
        return {
            "type": "ir.actions.act_window",
            "name": _("Choose the PDF receipt link"),
            "res_model": "usl.mail.pdf.candidate.wizard",
            "res_id": wizard.id,
            "view_mode": "form",
            "target": "new",
        }

    def action_retry_linked_receipt(self):
        self.ensure_one()
        self._latest_linked_receipt().with_user(self.env.user).action_retry()
        return {"type": "ir.actions.client", "tag": "reload"}

    def action_open_linked_receipt_website(self):
        self.ensure_one()
        retrieval = self._latest_linked_receipt().with_user(self.env.user)
        retrieval._check_can_open_handoff()
        return {
            "type": "ir.actions.act_url",
            "url": f"/usl/expenses/linked-receipt/{retrieval.id}/open",
            "target": "new",
        }

    def action_dismiss_linked_receipt(self):
        self.ensure_one()
        self._latest_linked_receipt().with_user(self.env.user).action_dismiss()
        return {"type": "ir.actions.client", "tag": "reload"}

    def attach_document(self, **kwargs):
        result = super().attach_document(**kwargs)
        self.env["usl.mail.pdf.retrieval"]._supersede_for_expense(self)
        return result

    def _message_post_after_hook(self, message, msg_values):
        result = super()._message_post_after_hook(message, msg_values)
        if len(self) != 1:
            return result
        Retrieval = self.env["usl.mail.pdf.retrieval"]
        if message.message_type == "email":
            Retrieval.sudo()._discover_for_expense(self, message)
        elif (
            not self.env.context.get("linked_receipt_attachment")
            and Retrieval._message_has_receipt(message)
        ):
            Retrieval._supersede_for_expense(self)
        return result
