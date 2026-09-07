import hashlib
import json
import os
import re
from email.utils import parseaddr
from urllib.parse import parse_qsl, unquote, urlsplit

from lxml import etree, html
from psycopg2 import IntegrityError

from odoo import _, api, fields, models
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
    "rate_limited",
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
        },
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
        "rate_limited": "The receipt provider is limiting download attempts.",
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
                _("Use the linked-receipt governance actions to change a host."),
            )
        return super().write(vals)

    def action_block(self):
        self.check_access("write")
        self.with_context(linked_receipt_internal=_LINKED_RECEIPT_INTERNAL).write({"state": "blocked"})

    def action_activate(self):
        self.check_access("write")
        for host in self:
            host.with_context(linked_receipt_internal=_LINKED_RECEIPT_INTERNAL).write(
                {"state": "active" if host.success_count else "provisional"},
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
                _("Use the linked-receipt governance actions to change a pattern."),
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
                        },
                    )
            except IntegrityError:
                pattern = self.sudo().search(
                    [("signature", "=", candidate["signature"])], limit=1,
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
            },
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
            },
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
                },
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
            "USL_LINKED_PDF_DOWNLOAD_ADMITTED", "0",
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
                    & set(POSITIVE_TOKENS),
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
                & SEMANTIC_TOKENS,
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
                },
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
                & {"facture", "invoice", "justificatif", "receipt", "recu", "reçu"},
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
                json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode(),
            ).hexdigest()
            matching = Pattern.search(
                [
                    ("sender_domain", "=", sender_domain),
                    ("hostname", "=", hostname),
                    ("state", "=", "active"),
                ],
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
                ),
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
                },
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
                ),
            ),
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
            },
        )
        if automatic:
            retrieval._select_candidate(top["fingerprint"], teach=False)
            retrieval._enqueue()
        return retrieval

    @api.model
    def _expense_is_eligible(self, expense):
        expense = expense.exists()
        return bool(expense and expense.state in ("draft", "approved"))

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
            },
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
                _("This receipt pattern is blocked for the Odoo instance."),
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
            },
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
                },
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
                _("This receipt pattern is blocked for the Odoo instance."),
            )
        host = self.env["usl.mail.pdf.host"].sudo().search(
            [("hostname", "=", self.starting_host)], limit=1,
        )
        if host.state == "blocked":
            raise UserError(_("This receipt host is blocked for the Odoo instance."))
        if pattern.state == "paused":
            pattern.with_context(linked_receipt_internal=_LINKED_RECEIPT_INTERNAL).write(
                {"state": "learning", "consecutive_failure_count": 0},
            )
        self.sudo().write(
            {
                "generation": self.generation + 1,
                "attempt_count": 0,
                "failure_code": False,
                "failure_message": False,
            },
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
            lambda item: item.id != self.source_message_id.id,
        )
        return any(self._message_has_receipt(message) for message in messages)

    @api.model
    def _supersede_for_expense(self, expense):
        retrievals = self.sudo().search(
            [
                ("expense_id", "=", expense.id),
                ("state", "in", ("selection_required", "queued", "running", "retrying", "needs_attention")),
            ],
        )
        for retrieval in retrievals:
            retrieval.write(
                {
                    "state": "superseded",
                    "generation": retrieval.generation + 1,
                },
            )
