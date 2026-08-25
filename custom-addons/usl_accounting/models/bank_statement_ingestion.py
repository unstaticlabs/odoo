import datetime as dt
import hashlib
import ipaddress
import logging
import mimetypes
import re
import socket
import urllib.parse
import urllib.request
import zipfile
from email import policy
from email.parser import BytesParser
from email.utils import parseaddr
from io import BytesIO
from pathlib import PurePosixPath

from lxml import html
from psycopg2 import IntegrityError

from odoo import _, Command, api, fields, models
from odoo.addons.base.models.res_partner_bank import sanitize_account_number
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import BinaryBytes

from .bank_statement_review import REVIEW_STATES


_logger = logging.getLogger(__name__)

MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 100
MAX_COMPRESSION_RATIO = 100


def _split_config_values(value):
    return {
        item.strip().lower().rstrip(".")
        for item in re.split(r"[\s,;]+", value or "")
        if item.strip()
    }


def _month_end(value):
    next_month = (value.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return next_month - dt.timedelta(days=1)


class AccountBankIngestionConfig(models.Model):
    _name = "account.bank.ingestion.config"
    _description = "Scheduled Bank Export Route"
    _inherit = ["mail.alias.mixin", "mail.thread", "mail.activity.mixin"]
    _check_company_auto = True
    _order = "company_id, journal_id"

    name = fields.Char(required=True, tracking=True)
    active = fields.Boolean(default=True, tracking=True)
    processing_enabled = fields.Boolean(
        string="Process received exports", default=False, tracking=True
    )
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company, index=True
    )
    journal_id = fields.Many2one(
        "account.journal",
        required=True,
        check_company=True,
        domain="[('type', '=', 'bank'), ('company_id', '=', company_id)]",
        tracking=True,
    )
    provider = fields.Selection(
        [("shine", "Shine"), ("other", "Other scheduled export")],
        default="shine",
        required=True,
        tracking=True,
    )
    source_account_identifier = fields.Char(required=True, tracking=True)
    allowed_senders = fields.Text(
        default="hello@shine.fr",
        help="Exact sender addresses separated by commas or line breaks.",
    )
    allowed_download_hosts = fields.Text(
        default="accounting.files.shine.fr",
        help="Exact HTTPS hosts separated by commas or line breaks.",
    )
    responsible_user_id = fields.Many2one(
        "res.users",
        string="Monthly review owner",
        required=True,
        default=lambda self: self.env.user,
    )
    automatic_start_date = fields.Date(required=True, tracking=True)
    expected_delivery_day = fields.Integer(default=5, required=True)
    ingestion_ids = fields.One2many("account.bank.ingestion", "config_id")
    statement_ids = fields.One2many("account.bank.statement", "ingestion_config_id")
    expected_period_start = fields.Date(compute="_compute_expected_review")
    expected_period_end = fields.Date(compute="_compute_expected_review")
    expected_delivery_date = fields.Date(compute="_compute_expected_review")
    review_status = fields.Selection(REVIEW_STATES, compute="_compute_expected_review")
    review_next_action = fields.Char(compute="_compute_expected_review")
    expected_statement_id = fields.Many2one(
        "account.bank.statement", compute="_compute_expected_review"
    )

    _active_journal_unique = models.UniqueIndex(
        "(journal_id) WHERE active IS TRUE"
    )

    @api.constrains("journal_id", "company_id")
    def _check_journal_company(self):
        for config in self:
            if config.journal_id.company_id != config.company_id:
                raise ValidationError(_("The bank journal must belong to the configured company."))

    @api.constrains("expected_delivery_day")
    def _check_delivery_day(self):
        if any(not 1 <= config.expected_delivery_day <= 28 for config in self):
            raise ValidationError(_("The expected delivery day must be between 1 and 28."))

    @api.constrains("allowed_senders")
    def _check_allowed_senders(self):
        for config in self:
            for sender in config._allowed_sender_set():
                if parseaddr(sender)[1].lower() != sender or "@" not in sender:
                    raise ValidationError(_("Use complete, exact sender email addresses."))

    @api.constrains("allowed_download_hosts")
    def _check_allowed_hosts(self):
        for config in self:
            for host in config._allowed_host_set():
                if "/" in host or ":" in host or not re.fullmatch(r"[a-z0-9.-]+", host):
                    raise ValidationError(_("Download hosts must be plain DNS host names."))

    def _allowed_sender_set(self):
        self.ensure_one()
        return _split_config_values(self.allowed_senders)

    def _allowed_host_set(self):
        self.ensure_one()
        return _split_config_values(self.allowed_download_hosts)

    def _alias_get_creation_values(self):
        values = super()._alias_get_creation_values()
        values.update(
            {
                "alias_model_id": self.env["ir.model"]._get_id("account.bank.ingestion"),
                "alias_contact": "everyone",
                "alias_defaults": repr({"config_id": self.id}),
            }
        )
        return values

    @api.depends(
        "automatic_start_date",
        "expected_delivery_day",
        "processing_enabled",
        "statement_ids.certification_state",
        "statement_ids.review_status",
        "ingestion_ids.state",
        "ingestion_ids.period_start",
        "ingestion_ids.period_end",
    )
    def _compute_expected_review(self):
        today = fields.Date.context_today(self)
        last_completed = (today.replace(day=1) - dt.timedelta(days=1))
        for config in self:
            exceptional_statement = config.statement_ids.filtered(
                lambda item: item.unresolved_exception_count
            ).sorted(lambda item: (item.period_start or today, item.id))[:1]
            if exceptional_statement:
                config.expected_period_start = exceptional_statement.period_start
                config.expected_period_end = exceptional_statement.period_end
                config.expected_delivery_date = exceptional_statement.period_end + dt.timedelta(
                    days=config.expected_delivery_day
                )
                config.expected_statement_id = exceptional_statement
                config.review_status = "attention"
                config.review_next_action = exceptional_statement.review_blocking_reason
                continue
            start = (config.automatic_start_date or last_completed).replace(day=1)
            period_start = start
            statement = self.env["account.bank.statement"]
            while period_start <= last_completed:
                period_end = _month_end(period_start)
                statement = config.statement_ids.filtered(
                    lambda item: item.period_start == period_start
                    and item.period_end == period_end
                )[:1]
                if not statement or statement.certification_state != "certified":
                    break
                period_start = period_end + dt.timedelta(days=1)
            if period_start > last_completed:
                period_start = last_completed.replace(day=1)
                statement = config.statement_ids.filtered(
                    lambda item: item.period_start == period_start
                )[:1]
            period_end = _month_end(period_start)
            delivery_month = period_end + dt.timedelta(days=1)
            delivery_date = delivery_month.replace(day=config.expected_delivery_day)
            config.expected_period_start = period_start
            config.expected_period_end = period_end
            config.expected_delivery_date = delivery_date
            config.expected_statement_id = statement
            if statement:
                config.review_status = statement.review_status
                config.review_next_action = statement.review_blocking_reason or _("Open the statement review.")
            else:
                ingestions = config.ingestion_ids.filtered(
                    lambda item: item.period_start == period_start
                    and item.period_end == period_end
                )
                if any(item.state in ("received", "processing") for item in ingestions):
                    config.review_status = "processing"
                    config.review_next_action = _("The received bank export is being processed.")
                elif any(item.state in ("attention", "failed") for item in ingestions):
                    config.review_status = "attention"
                    config.review_next_action = _("Review the received bank export.")
                else:
                    config.review_status = "expected"
                    config.review_next_action = (
                        _("The scheduled export is overdue.")
                        if today > delivery_date
                        else _("Waiting for the scheduled bank export.")
                    )

    def action_open_expected_statement(self):
        self.ensure_one()
        if self.expected_statement_id:
            return {
                "type": "ir.actions.act_window",
                "res_model": "account.bank.statement",
                "res_id": self.expected_statement_id.id,
                "view_mode": "form",
            }
        return {
            "type": "ir.actions.act_window",
            "name": _("Received bank exports"),
            "res_model": "account.bank.ingestion",
            "view_mode": "list,form",
            "domain": [("config_id", "=", self.id)],
            "context": {"create": False},
        }

    @api.model
    def _cron_update_expected_activities(self):
        for config in self.search([("active", "=", True)]):
            config._sync_review_activity()

    def _sync_review_activity(self):
        self.ensure_one()
        model_id = self.env["ir.model"]._get_id("account.journal")
        summary = _("Review scheduled bank statement")
        activities = self.env["mail.activity"].search(
            [
                ("res_model_id", "=", model_id),
                ("res_id", "=", self.journal_id.id),
                ("summary", "=", summary),
            ]
        )
        needs_activity = self.review_status == "attention" or (
            self.review_status == "expected"
            and fields.Date.context_today(self) > self.expected_delivery_date
        )
        if needs_activity and not activities:
            self.journal_id.activity_schedule(
                "mail.mail_activity_data_todo",
                user_id=self.responsible_user_id.id,
                date_deadline=fields.Date.context_today(self),
                summary=summary,
                note=self.review_next_action,
            )
        elif not needs_activity and activities:
            activities.action_feedback(feedback=_("Bank statement follow-up resolved."))


class AccountJournal(models.Model):
    _inherit = "account.journal"

    bank_ingestion_config_id = fields.Many2one(
        "account.bank.ingestion.config", compute="_compute_bank_ingestion_review"
    )
    bank_statement_review_status = fields.Selection(
        REVIEW_STATES, compute="_compute_bank_ingestion_review"
    )
    bank_statement_review_period = fields.Char(compute="_compute_bank_ingestion_review")
    bank_statement_review_next_action = fields.Char(
        compute="_compute_bank_ingestion_review"
    )

    def _compute_bank_ingestion_review(self):
        configs = self.env["account.bank.ingestion.config"].search(
            [("journal_id", "in", self.ids), ("active", "=", True)]
        )
        by_journal = {config.journal_id.id: config for config in configs}
        for journal in self:
            config = by_journal.get(journal.id)
            journal.bank_ingestion_config_id = config
            journal.bank_statement_review_status = config.review_status if config else False
            journal.bank_statement_review_period = (
                f"{config.expected_period_start:%b %Y}" if config and config.expected_period_start else False
            )
            journal.bank_statement_review_next_action = config.review_next_action if config else False

    def action_open_bank_statement_review(self):
        self.ensure_one()
        if not self.bank_ingestion_config_id:
            raise UserError(_("No scheduled bank export route is configured for this journal."))
        return self.bank_ingestion_config_id.action_open_expected_statement()


class AccountBankIngestion(models.Model):
    _name = "account.bank.ingestion"
    _description = "Received Bank Export"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _check_company_auto = True
    _order = "received_at desc, id desc"

    name = fields.Char(required=True, tracking=True)
    config_id = fields.Many2one(
        "account.bank.ingestion.config", required=True, ondelete="restrict", check_company=True, index=True
    )
    company_id = fields.Many2one(
        "res.company", related="config_id.company_id", store=True, index=True
    )
    journal_id = fields.Many2one(
        "account.journal", related="config_id.journal_id", store=True, check_company=True, index=True
    )
    message_id_header = fields.Char(string="Source Message-ID", copy=False, index=True)
    sender = fields.Char(copy=False)
    recipient = fields.Char(copy=False)
    subject = fields.Char(copy=False)
    headers = fields.Text(copy=False, groups="account.group_account_manager")
    body_html = fields.Html(copy=False, sanitize=False, groups="account.group_account_manager")
    received_at = fields.Datetime(default=fields.Datetime.now, required=True, copy=False)
    period_start = fields.Date(copy=False, index=True)
    period_end = fields.Date(copy=False, index=True)
    state = fields.Selection(
        [("received", "Received"), ("processing", "Processing"), ("done", "Processed"), ("attention", "Needs attention"), ("failed", "Import failed")],
        default="received",
        required=True,
        tracking=True,
        index=True,
    )
    attempt_count = fields.Integer(default=0, copy=False)
    duplicate_delivery_count = fields.Integer(default=0, copy=False)
    last_attempt_at = fields.Datetime(copy=False)
    last_error = fields.Text(copy=False)
    file_ids = fields.One2many("account.bank.ingestion.file", "ingestion_id")
    statement_ids = fields.Many2many(
        "account.bank.statement", compute="_compute_statements"
    )
    unresolved_exception_count = fields.Integer(compute="_compute_statements")

    _message_config_unique = models.UniqueIndex(
        "(config_id, message_id_header) WHERE message_id_header IS NOT NULL"
    )

    @api.depends("file_ids.statement_id", "file_ids.exception_ids.state")
    def _compute_statements(self):
        for ingestion in self:
            ingestion.statement_ids = ingestion.file_ids.statement_id
            ingestion.unresolved_exception_count = len(
                ingestion.file_ids.exception_ids.filtered(lambda item: item.state == "open")
            )

    @api.model
    def message_new(self, msg_dict, custom_values=None):
        values = dict(custom_values or {})
        config = self.env["account.bank.ingestion.config"].browse(values.get("config_id")).exists()
        if not config:
            raise ValidationError(_("The bank export email route is not configured."))
        sender = parseaddr(msg_dict.get("email_from") or "")[1].strip().lower()
        period_start, period_end = self._period_from_text(msg_dict.get("subject") or "")
        message_id = (msg_dict.get("message_id") or "").strip() or False
        existing = message_id and self.sudo().search(
            [("config_id", "=", config.id), ("message_id_header", "=", message_id)], limit=1
        )
        if existing:
            existing.sudo().write(
                {"duplicate_delivery_count": existing.duplicate_delivery_count + 1}
            )
            return existing
        values.update(
            {
                "name": msg_dict.get("subject") or _("Received bank export"),
                "config_id": config.id,
                "message_id_header": message_id,
                "sender": sender,
                "recipient": msg_dict.get("to"),
                "subject": msg_dict.get("subject"),
                "headers": repr(msg_dict.get("headers") or {}),
                "body_html": msg_dict.get("body"),
                "received_at": fields.Datetime.now(),
                "period_start": period_start,
                "period_end": period_end,
            }
        )
        try:
            with self.env.cr.savepoint():
                return self.create(values)
        except IntegrityError:
            if not message_id:
                raise
            existing = self.sudo().search(
                [("config_id", "=", config.id), ("message_id_header", "=", message_id)], limit=1
            )
            if not existing:
                raise
            existing.sudo().write(
                {"duplicate_delivery_count": existing.duplicate_delivery_count + 1}
            )
            return existing

    @api.model
    def _period_from_text(self, value):
        matches = re.findall(r"(\d{2})[/-](\d{2})[/-](\d{4})", value or "")
        if len(matches) < 2:
            return False, False
        try:
            dates = [dt.date(int(year), int(month), int(day)) for day, month, year in matches[:2]]
        except ValueError:
            return False, False
        return min(dates), max(dates)

    def action_process_now(self):
        if not self.env.user.has_group("account.group_account_user"):
            raise AccessError(_("Only an accountant can process a received bank export."))
        for ingestion in self:
            ingestion.sudo()._process()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "message": _("Bank export processing finished."),
                "type": "warning" if any(item.state in ("attention", "failed") for item in self) else "success",
                "sticky": False,
            },
        }

    def action_retry(self):
        return self.action_process_now()

    @api.model
    def _cron_process_pending(self, limit=10):
        self.env.cr.execute(
            """
            SELECT id
              FROM account_bank_ingestion
             WHERE state IN ('received', 'failed')
             ORDER BY received_at, id
             FOR UPDATE SKIP LOCKED
             LIMIT %s
            """,
            [limit],
        )
        for ingestion in self.browse([row[0] for row in self.env.cr.fetchall()]):
            if not ingestion.config_id.processing_enabled:
                continue
            with self.env.cr.savepoint():
                ingestion.with_context(bank_ingestion_cron=True)._process()

    def _process(self):
        self.ensure_one()
        if not self.config_id.active:
            raise UserError(_("This bank export route is archived."))
        if not self.config_id.processing_enabled and self.env.context.get("bank_ingestion_cron"):
            return
        self.env.cr.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            [f"account.bank.ingestion:{self.id}"],
        )
        self.write(
            {
                "state": "processing",
                "attempt_count": self.attempt_count + 1,
                "last_attempt_at": fields.Datetime.now(),
                "last_error": False,
            }
        )
        try:
            self._validate_sender()
            self._retain_message_attachments()
            self._download_configured_links()
            self._extract_archives()
            files = self.file_ids.filtered(lambda item: item.classification not in ("email", "zip"))
            ofx_files = files.filtered(lambda item: item.classification == "ofx")
            for source_file in ofx_files:
                source_file._process_isolated()
            for source_file in files.filtered(lambda item: item.classification == "pdf"):
                source_file._process_isolated()
            for source_file in files.filtered(lambda item: item.classification == "unsupported"):
                source_file._ensure_exception(
                    "unsupported",
                    _("Unsupported bank export attachment"),
                    _("The attachment %(name)s needs an accounting review.", name=source_file.filename),
                )
                source_file.processing_state = "attention"
            if not ofx_files:
                self._ensure_exception(
                    "import",
                    _("OFX transaction export missing"),
                    _("No OFX transaction export was found. CSV and QIF copies were retained but not imported."),
                )
            open_exceptions = self.file_ids.exception_ids.filtered(lambda item: item.state == "open")
            self.state = "attention" if open_exceptions else "done"
            self.message_post(
                body=(
                    _("Bank export retained and processed; review is required.")
                    if open_exceptions
                    else _("Bank export retained and processed successfully.")
                )
            )
        except Exception as error:
            _logger.info("Bank export processing failed for ingestion %s: %s", self.id, type(error).__name__)
            self.write({"state": "failed", "last_error": str(error)})
            self._ensure_exception(
                "import", _("Bank export processing failed"), str(error)
            )
        finally:
            self.config_id._sync_review_activity()

    def _validate_sender(self):
        self.ensure_one()
        if self.sender not in self.config_id._allowed_sender_set():
            raise UserError(
                _("The sender %(sender)s is not approved for this bank export route.", sender=self.sender or _("unknown"))
            )

    def _retain_message_attachments(self):
        Attachment = self.env["ir.attachment"].sudo()
        attachments = Attachment.search(
            [("res_model", "=", self._name), ("res_id", "=", self.id)]
        )
        for attachment in attachments:
            if not attachment.raw:
                continue
            self.env["account.bank.ingestion.file"]._from_attachment(self, attachment)

    def _download_configured_links(self):
        self.ensure_one()
        if not self.body_html:
            return
        try:
            document = html.fromstring(self.body_html)
            hrefs = document.xpath("//a/@href")
        except (TypeError, ValueError):
            hrefs = re.findall(r"https://[^\s<'\"]+", self.body_html)
        allowed_hosts = self.config_id._allowed_host_set()
        existing_hosts = set(self.file_ids.mapped("download_host"))
        for href in hrefs:
            parsed = urllib.parse.urlsplit(href)
            host = (parsed.hostname or "").lower().rstrip(".")
            if parsed.scheme != "https" or host not in allowed_hosts or host in existing_hosts:
                continue
            content, filename, mimetype = self._download_https(href, host)
            attachment = self.env["ir.attachment"].sudo().create(
                {
                    "name": filename,
                    "raw": content,
                    "mimetype": mimetype,
                    "res_model": self._name,
                    "res_id": self.id,
                    "company_id": self.company_id.id,
                }
            )
            self.env["account.bank.ingestion.file"]._from_attachment(
                self, attachment, download_host=host
            )
            existing_hosts.add(host)

    def _download_https(self, url, expected_host):
        self._validate_public_host(expected_host)

        class SameHostRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(handler_self, request, fp, code, message, headers, new_url):
                redirected = urllib.parse.urlsplit(new_url)
                if redirected.scheme != "https" or (redirected.hostname or "").lower().rstrip(".") != expected_host:
                    raise UserError(_("The bank export download redirected to an unapproved host."))
                return super().redirect_request(request, fp, code, message, headers, new_url)

        request = urllib.request.Request(url, headers={"User-Agent": "USL-Odoo-Bank-Export/1"})
        with urllib.request.build_opener(SameHostRedirect()).open(request, timeout=20) as response:
            declared = int(response.headers.get("Content-Length") or 0)
            if declared > MAX_DOWNLOAD_BYTES:
                raise UserError(_("The bank export download exceeds the 50 MiB limit."))
            chunks = []
            size = 0
            while True:
                chunk = response.read(min(1024 * 1024, MAX_DOWNLOAD_BYTES + 1 - size))
                if not chunk:
                    break
                chunks.append(chunk)
                size += len(chunk)
                if size > MAX_DOWNLOAD_BYTES:
                    raise UserError(_("The bank export download exceeds the 50 MiB limit."))
            disposition = response.headers.get("Content-Disposition") or ""
            filename_match = re.search(r"filename\*?=(?:UTF-8''|\")?([^\";]+)", disposition, re.I)
            filename = urllib.parse.unquote(filename_match.group(1)) if filename_match else PurePosixPath(urllib.parse.urlsplit(url).path).name
            filename = PurePosixPath(filename or "bank-export.zip").name
            mimetype = response.headers.get_content_type() or mimetypes.guess_type(filename)[0]
            return b"".join(chunks), filename, mimetype or "application/octet-stream"

    def _validate_public_host(self, host):
        for info in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM):
            address = ipaddress.ip_address(info[4][0])
            if not address.is_global:
                raise UserError(_("The configured download host does not resolve to a public address."))

    def _extract_archives(self):
        for archive in self.file_ids.filtered(lambda item: item.classification == "zip" and item.processing_state == "pending"):
            archive._extract_zip()

    def _ensure_exception(self, kind, name, detail, file=False, statement=False):
        existing = self.env["account.bank.statement.exception"].search(
            [
                ("ingestion_id", "=", self.id),
                ("file_id", "=", file.id if file else False),
                ("kind", "=", kind),
                ("name", "=", name),
                ("state", "=", "open"),
            ],
            limit=1,
        )
        return existing or self.env["account.bank.statement.exception"].create(
            {
                "ingestion_id": self.id,
                "file_id": file.id if file else False,
                "statement_id": statement.id if statement else False,
                "company_id": self.company_id.id,
                "kind": kind,
                "name": name,
                "detail": detail,
            }
        )


class AccountBankIngestionFile(models.Model):
    _name = "account.bank.ingestion.file"
    _description = "Retained Bank Export File"
    _order = "ingestion_id desc, id"
    _check_company_auto = True

    ingestion_id = fields.Many2one(
        "account.bank.ingestion", required=True, ondelete="restrict", check_company=True, index=True
    )
    company_id = fields.Many2one(
        "res.company", related="ingestion_id.company_id", store=True, index=True
    )
    attachment_id = fields.Many2one(
        "ir.attachment", required=True, ondelete="restrict", copy=False
    )
    filename = fields.Char(required=True, copy=False)
    mimetype = fields.Char(copy=False)
    sha256 = fields.Char(required=True, copy=False, index=True)
    size = fields.Integer(required=True, copy=False)
    classification = fields.Selection(
        [("email", "Source email"), ("zip", "Original export archive"), ("ofx", "OFX transactions"), ("pdf", "Official bank statement"), ("csv", "CSV copy"), ("qif", "QIF copy"), ("unsupported", "Needs review")],
        required=True,
        index=True,
    )
    processing_state = fields.Selection(
        [("pending", "Pending"), ("processed", "Processed"), ("duplicate", "Already imported"), ("attention", "Needs attention"), ("failed", "Failed")],
        default="pending",
        required=True,
        index=True,
    )
    processing_detail = fields.Text(copy=False)
    download_host = fields.Char(copy=False, groups="account.group_account_manager")
    parent_archive_id = fields.Many2one(
        "account.bank.ingestion.file", ondelete="restrict", check_company=True
    )
    extracted_file_ids = fields.One2many(
        "account.bank.ingestion.file", "parent_archive_id", readonly=True
    )
    statement_id = fields.Many2one(
        "account.bank.statement", ondelete="restrict", check_company=True, index=True
    )
    statement_line_ids = fields.Many2many(
        "account.bank.statement.line",
        "account_bank_line_ingestion_file_rel",
        "file_id",
        "line_id",
        readonly=True,
    )
    period_start = fields.Date(copy=False, index=True)
    period_end = fields.Date(copy=False, index=True)
    evidence_status = fields.Selection(
        [
            ("candidate", "Candidate"),
            ("accepted", "Accepted"),
            ("superseded", "Prior evidence"),
            ("duplicate", "Duplicate copy"),
        ],
        copy=False,
    )
    paperless_version = fields.Char(copy=False, readonly=True)
    exception_ids = fields.One2many("account.bank.statement.exception", "file_id")

    _attachment_unique = models.UniqueIndex("(attachment_id)")
    _accepted_statement_unique = models.UniqueIndex(
        "(statement_id) WHERE evidence_status = 'accepted'"
    )

    @api.model
    def _from_attachment(
        self,
        ingestion,
        attachment,
        download_host=False,
        parent_archive=False,
        forced_classification=False,
    ):
        existing = self.search([("attachment_id", "=", attachment.id)], limit=1)
        if existing:
            return existing
        content = bytes(attachment.raw or b"")
        return self.create(
            {
                "ingestion_id": ingestion.id,
                "attachment_id": attachment.id,
                "filename": attachment.name or _("Unnamed attachment"),
                "mimetype": attachment.mimetype,
                "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content),
                "classification": forced_classification or self._classify(attachment.name, attachment.mimetype, content),
                "download_host": download_host,
                "parent_archive_id": parent_archive.id if parent_archive else False,
            }
        )

    @api.model
    def _classify(self, filename, mimetype, content):
        lower = (filename or "").lower()
        if lower.endswith(".eml") or (mimetype or "").lower() in ("message/rfc822", "text/rfc822-headers"):
            return "email"
        if content.startswith(b"PK\x03\x04") and lower.endswith(".zip"):
            return "zip"
        if content.startswith(b"%PDF-") and lower.endswith(".pdf"):
            return "pdf"
        sample = content[:4096].lstrip().upper()
        if lower.endswith(".ofx") and (b"<OFX" in sample or sample.startswith(b"OFXHEADER:")):
            return "ofx"
        if lower.endswith(".csv"):
            return "csv"
        if lower.endswith(".qif") and sample.startswith(b"!TYPE"):
            return "qif"
        return "unsupported"

    def _content(self):
        self.ensure_one()
        content = bytes(self.attachment_id.sudo().raw or b"")
        if hashlib.sha256(content).hexdigest() != self.sha256:
            raise UserError(_("The retained source file no longer matches its recorded checksum."))
        return content

    def _extract_zip(self):
        self.ensure_one()
        content = self._content()
        try:
            archive = zipfile.ZipFile(BytesIO(content))
        except zipfile.BadZipFile as error:
            self.write({"processing_state": "failed", "processing_detail": _("The export archive is malformed.")})
            self._ensure_exception("import", _("Malformed export archive"), str(error))
            return
        members = archive.infolist()
        if len(members) > MAX_ARCHIVE_MEMBERS:
            raise UserError(_("The export archive contains too many files."))
        total = 0
        for member in members:
            path = PurePosixPath(member.filename)
            mode = member.external_attr >> 16
            if path.is_absolute() or ".." in path.parts or (mode & 0o170000) == 0o120000:
                raise UserError(_("The export archive contains an unsafe path or link."))
            if member.is_dir():
                continue
            total += member.file_size
            if total > MAX_ARCHIVE_BYTES:
                raise UserError(_("The uncompressed export exceeds the 100 MiB limit."))
            if member.compress_size and member.file_size / member.compress_size > MAX_COMPRESSION_RATIO:
                raise UserError(_("The export archive contains an unsafe compression ratio."))
            member_content = archive.read(member)
            filename = path.name
            nested_kind = self._classify(filename, mimetypes.guess_type(filename)[0], member_content)
            if nested_kind == "zip":
                nested_kind = "unsupported"
            attachment = self.env["ir.attachment"].sudo().create(
                {
                    "name": filename,
                    "raw": member_content,
                    "mimetype": mimetypes.guess_type(filename)[0] or "application/octet-stream",
                    "res_model": self.ingestion_id._name,
                    "res_id": self.ingestion_id.id,
                    "company_id": self.company_id.id,
                }
            )
            self._from_attachment(
                self.ingestion_id,
                attachment,
                parent_archive=self,
                forced_classification=nested_kind,
            )
        self.write({"processing_state": "processed", "processing_detail": _("Archive retained and safely extracted.")})

    def _process_isolated(self):
        self.ensure_one()
        try:
            with self.env.cr.savepoint():
                if self.classification == "ofx":
                    self._process_ofx()
                elif self.classification == "pdf":
                    self._associate_pdf()
        except Exception as error:
            self.write({"processing_state": "failed", "processing_detail": str(error)})
            self._ensure_exception("import", _("Attachment processing failed"), str(error))

    def _process_ofx(self):
        self.ensure_one()
        content = self._content()
        wizard = self.env["account.statement.import"].with_context(
            journal_id=self.ingestion_id.journal_id.id
        ).create(
            {"statement_file": BinaryBytes(content), "statement_filename": self.filename}
        )
        ofx = wizard._check_ofx(content)
        if not ofx:
            raise UserError(_("The OFX attachment is malformed or unsupported."))
        parsed_accounts = wizard._parse_file(content)
        if len(parsed_accounts) != 1 or len(ofx.accounts) != 1:
            raise UserError(_("The bank export must contain exactly one bank account."))
        currency_code, account_number, statements_values = parsed_accounts[0]
        config = self.ingestion_id.config_id
        if sanitize_account_number(account_number) != sanitize_account_number(config.source_account_identifier):
            self._ensure_exception(
                "account",
                _("Bank account does not match"),
                _("The OFX account does not match the account configured for this route."),
            )
            self.processing_state = "attention"
            return
        currency = wizard._match_currency(currency_code)
        journal_currency = config.journal_id.currency_id or config.company_id.currency_id
        if currency != journal_currency:
            raise UserError(_("The OFX currency does not match the configured bank journal."))
        if len(statements_values) != 1:
            raise UserError(_("The OFX export contains an ambiguous statement population."))
        values = wizard._complete_stmts_vals(
            statements_values, config.journal_id, account_number
        )[0]
        transactions = values.pop("transactions")
        raw_transactions = list(ofx.accounts[0].statement.transactions)
        if len(transactions) != len(raw_transactions):
            raise UserError(_("The OFX transaction population could not be verified."))
        dates = [fields.Date.to_date(item["date"]) for item in transactions]
        if not dates:
            raise UserError(_("The OFX export contains no transactions."))
        period_start = min(dates).replace(day=1)
        period_end = _month_end(max(dates))
        if period_start != max(dates).replace(day=1):
            raise UserError(_("One OFX file must cover a single calendar month."))
        if period_start < config.automatic_start_date.replace(day=1):
            raise UserError(_("This export predates the configured ingestion cut-over."))
        self.env.cr.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            [f"account.bank.period:{config.id}:{period_start.isoformat()}"],
        )
        raw_ids = [(str(item.id).strip() if item.id is not None else "") for item in raw_transactions]
        duplicate_ids = {item for item in raw_ids if item and raw_ids.count(item) > 1}
        new_values = []
        existing_lines = self.env["account.bank.statement.line"]
        ambiguous = []
        for ordinal, (line_values, raw_id) in enumerate(zip(transactions, raw_ids), start=1):
            line_values = dict(line_values)
            line_values["date"] = fields.Date.to_date(line_values["date"])
            if not raw_id or raw_id in duplicate_ids:
                fallback = hashlib.sha256(
                    f"{self.sha256}:{sanitize_account_number(account_number)}:{period_start}:{ordinal}".encode()
                ).hexdigest()
                candidate = {
                    **line_values,
                    "date": line_values["date"].isoformat(),
                    "provider_code": config.provider,
                    "provider_account_id": sanitize_account_number(account_number),
                    "provider_transaction_id": f"fallback:{fallback}",
                }
                ambiguous.append((ordinal, raw_id, candidate))
                continue
            existing = self._find_existing_transaction(
                config, raw_id, line_values["unique_import_id"], line_values["date"]
            )
            if existing:
                if (
                    existing.currency_id.compare_amounts(existing.amount, line_values["amount"]) != 0
                    or existing.date != line_values["date"]
                ):
                    raise UserError(_("A bank transaction identity already exists with different accounting facts."))
                existing_lines |= existing
                continue
            line_values.update(
                {
                    "provider_code": config.provider,
                    "provider_account_id": sanitize_account_number(account_number),
                    "provider_transaction_id": raw_id,
                    "provider_identity_kind": "stable",
                    "transaction_details": {
                        "provider": config.provider,
                        "account_id": sanitize_account_number(account_number),
                        "transaction_id": raw_id,
                    },
                    "ingestion_file_ids": [Command.link(self.id)],
                }
            )
            line_values["sequence"] = ordinal
            new_values.append(line_values)
        statement = self._get_or_create_statement(
            config, period_start, period_end, values, existing_lines, new_values
        )
        if statement:
            for line in existing_lines:
                update = {"ingestion_file_ids": [Command.link(self.id)]}
                if not line.provider_transaction_id:
                    raw_id = next(
                        (
                            raw
                            for parsed, raw in zip(transactions, raw_ids)
                            if parsed["unique_import_id"] == line.unique_import_id
                            or self._historical_extra_id(line) == raw
                        ),
                        False,
                    )
                    if raw_id:
                        update.update(
                            {
                                "provider_code": config.provider,
                                "provider_account_id": sanitize_account_number(account_number),
                                "provider_transaction_id": raw_id,
                                "provider_identity_kind": "stable",
                            }
                        )
                line.with_context(bank_review_internal=True).write(update)
            self.write(
                {
                    "statement_id": statement.id,
                    "period_start": period_start,
                    "period_end": period_end,
                    "processing_state": "duplicate" if not new_values else "processed",
                    "processing_detail": (
                        _("All %(count)s bank transactions were already present and linked.", count=len(existing_lines))
                        if not new_values
                        else _("Imported %(new)s new transaction(s); %(existing)s already present.", new=len(new_values), existing=len(existing_lines))
                    ),
                }
            )
            self._associate_period_pdfs(statement)
        for ordinal, raw_id, candidate in ambiguous:
            exception = self._ensure_exception(
                "identity",
                _("Transaction %(ordinal)s needs an identity decision", ordinal=ordinal),
                (
                    _("The OFX transaction identifier is duplicated in this file.")
                    if raw_id in duplicate_ids
                    else _("The OFX transaction has no stable bank identifier.")
                ),
                statement=statement,
            )
            exception.candidate_values = candidate
        if ambiguous:
            self.processing_state = "attention"

    def _find_existing_transaction(self, config, raw_id, unique_import_id, transaction_date):
        Line = self.env["account.bank.statement.line"].sudo()
        existing = Line.search(
            [
                ("journal_id", "=", config.journal_id.id),
                ("provider_code", "=", config.provider),
                ("provider_account_id", "=", sanitize_account_number(config.source_account_identifier)),
                ("provider_transaction_id", "=", raw_id),
            ],
            limit=1,
        )
        if not existing:
            existing = Line.search(
                [("journal_id", "=", config.journal_id.id), ("unique_import_id", "=", unique_import_id)],
                limit=1,
            )
        if not existing:
            candidates = Line.search(
                [("journal_id", "=", config.journal_id.id), ("date", "=", transaction_date)]
            )
            exact = candidates.filtered(lambda line: self._historical_extra_id(line) == raw_id)
            if len(exact) > 1:
                raise UserError(_("The migrated bank history contains a conflicting transaction identity."))
            existing = exact[:1]
            if existing and not existing.unique_import_id:
                existing.with_context(bank_review_internal=True).unique_import_id = unique_import_id
        return existing

    @api.model
    def _historical_extra_id(self, line):
        details = line.transaction_details or {}
        return str((details.get("extra") or {}).get("id") or "").strip()

    def _get_or_create_statement(self, config, period_start, period_end, values, existing_lines, new_values):
        Statement = self.env["account.bank.statement"]
        statement = Statement.search(
            [
                ("ingestion_config_id", "=", config.id),
                ("period_start", "=", period_start),
                ("period_end", "=", period_end),
            ],
            limit=1,
        )
        existing_statements = existing_lines.statement_id
        if not statement and existing_statements:
            if len(existing_statements) != 1:
                raise UserError(_("Existing transactions are split across multiple bank statements."))
            candidate = existing_statements
            candidate_dates = candidate.line_ids.filtered(lambda line: line.state == "posted").mapped("date")
            if candidate.ingestion_config_id or not candidate_dates or min(candidate_dates).replace(day=1) != period_start or _month_end(max(candidate_dates)) != period_end:
                raise UserError(_("The existing statement cannot be adopted without changing historical membership."))
            candidate.with_context(bank_review_internal=True).write(
                {"ingestion_config_id": config.id, "period_start": period_start, "period_end": period_end}
            )
            statement = candidate
        if not statement and not new_values and not existing_lines:
            return Statement
        create_commands = [Command.create(value) for value in new_values]
        if not statement:
            statement_values = {
                "name": _("%(journal)s — %(month)s", journal=config.journal_id.code, month=period_start.strftime("%B %Y")),
                "reference": self.filename,
                "ingestion_config_id": config.id,
                "period_start": period_start,
                "period_end": period_end,
                "balance_start": values.get("balance_start", 0),
                "balance_end_real": values.get("balance_end_real", 0),
                "line_ids": [Command.set(existing_lines.ids), *create_commands],
            }
            statement = Statement.with_context(bank_review_internal=True).create(statement_values)
        elif create_commands:
            statement.with_context(bank_review_internal=True).write(
                {"line_ids": create_commands}
            )
        outside = existing_lines.filtered(
            lambda line: line.statement_id and line.statement_id != statement
        )
        if outside:
            raise UserError(_("An exact transaction identity already belongs to another statement."))
        unassigned = existing_lines.filtered(lambda line: not line.statement_id)
        if unassigned:
            unassigned.with_context(bank_review_internal=True).write(
                {"statement_id": statement.id}
            )
        if not statement.balances_confirmed:
            statement.with_context(bank_review_internal=True).write(
                {
                    "balance_start": values.get("balance_start", statement.balance_start),
                    "balance_end_real": values.get("balance_end_real", statement.balance_end_real),
                }
            )
        return statement

    def _associate_period_pdfs(self, statement):
        candidates = self.search(
            [
                ("ingestion_id.config_id", "=", self.ingestion_id.config_id.id),
                ("classification", "=", "pdf"),
                ("period_start", "=", statement.period_start),
                ("period_end", "=", statement.period_end),
            ],
            order="id",
        )
        for candidate in candidates:
            candidate.statement_id = statement
            if (
                statement.accepted_evidence_id
                and candidate != statement.accepted_evidence_id
                and candidate.sha256 == statement.accepted_evidence_id.sha256
            ):
                candidate.write(
                    {
                        "evidence_status": "duplicate",
                        "processing_state": "duplicate",
                        "processing_detail": _("This exact PDF is already the accepted evidence."),
                    }
                )
            elif not candidate.evidence_status:
                candidate.evidence_status = "candidate"
        if not statement.accepted_evidence_id and candidates:
            candidates[0]._accept_evidence()

    def _associate_pdf(self):
        self.ensure_one()
        period_start = self.ingestion_id.period_start
        period_end = self.ingestion_id.period_end
        if not period_start or not period_end:
            self.write(
                {
                    "processing_state": "attention",
                    "processing_detail": _("The statement period could not be determined from the email subject."),
                    "evidence_status": "candidate",
                }
            )
            self._ensure_exception(
                "evidence",
                _("Confirm the PDF statement period"),
                _("Set an unambiguous period on the received export, then retry."),
            )
            return
        self.write(
            {
                "period_start": period_start,
                "period_end": period_end,
                "processing_state": "processed",
                "processing_detail": _("Official PDF retained unchanged."),
                "evidence_status": "candidate",
            }
        )
        statement = self.env["account.bank.statement"].search(
            [
                ("ingestion_config_id", "=", self.ingestion_id.config_id.id),
                ("period_start", "=", period_start),
                ("period_end", "=", period_end),
            ],
            limit=1,
        )
        if statement:
            self.statement_id = statement
            if not statement.accepted_evidence_id:
                self._accept_evidence()
            elif statement.accepted_evidence_id.sha256 == self.sha256:
                self.write(
                    {
                        "evidence_status": "duplicate",
                        "processing_state": "duplicate",
                        "processing_detail": _("This exact PDF is already the accepted evidence."),
                    }
                )
            else:
                self._ensure_exception(
                    "evidence",
                    _("Replacement bank statement received"),
                    _("A new PDF was retained. Review it before replacing the accepted evidence."),
                    statement=statement,
                )

    def action_accept_evidence(self):
        if not self.env.user.has_group("account.group_account_user"):
            raise AccessError(_("Only an accountant can accept bank statement evidence."))
        for source_file in self:
            source_file._accept_evidence()
        return True

    def action_download(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_url",
            "url": f"/web/content/{self.attachment_id.id}?download=1",
            "target": "self",
        }

    def _accept_evidence(self):
        self.ensure_one()
        if self.classification != "pdf" or not self.statement_id:
            raise UserError(_("This PDF is not linked to a bank statement period."))
        statement = self.statement_id
        self.env.cr.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            [f"account.bank.statement.evidence:{statement.id}"],
        )
        self.invalidate_recordset()
        statement.invalidate_recordset()
        if statement.certification_state == "certified" and statement.accepted_evidence_id != self:
            raise UserError(_("Reopen the certified statement before accepting replacement evidence."))
        previous = statement.accepted_evidence_id
        if previous == self:
            return
        if previous:
            previous.sudo().evidence_status = "superseded"
        self.sudo().evidence_status = "accepted"
        statement.sudo().with_context(bank_review_internal=True).write(
            {
                "accepted_evidence_id": self.id,
                "attachment_ids": [Command.link(self.attachment_id.id)],
            }
        )
        statement.message_post(
            body=_("Official bank statement evidence accepted: %(name)s", name=self.filename),
            attachment_ids=[self.attachment_id.id],
        )
        self._bank_evidence_accepted()

    def _bank_evidence_accepted(self):
        """Optional bridge hook for durable document archives."""
        return None

    def _ensure_exception(self, kind, name, detail, statement=False):
        return self.ingestion_id._ensure_exception(
            kind, name, detail, file=self, statement=statement or self.statement_id
        )

    def write(self, vals):
        immutable = {
            "ingestion_id",
            "attachment_id",
            "filename",
            "mimetype",
            "sha256",
            "size",
            "classification",
            "parent_archive_id",
        }
        if immutable.intersection(vals) and self.filtered(lambda record: record.id):
            raise AccessError(_("Retained source file identity is immutable."))
        operational = {
            "processing_state",
            "processing_detail",
            "statement_id",
            "period_start",
            "period_end",
            "evidence_status",
            "paperless_version",
        }
        if operational.intersection(vals) and not self.env.su:
            raise AccessError(_("Use the bank export review actions to change source processing or evidence state."))
        return super().write(vals)

    def unlink(self):
        raise AccessError(_("Retained bank export files cannot be deleted."))


class MailThread(models.AbstractModel):
    _inherit = "mail.thread"

    @api.model
    def message_process(
        self,
        model,
        message,
        custom_values=None,
        save_original=False,
        strip_attachments=False,
        thread_id=None,
    ):
        # A direct target is used by tests and mailgate integrations. Alias
        # deliveries normally pass no fallback model, so detect only recipients
        # matching this module's configured aliases before opting into RFC822
        # retention. No other route changes its storage behavior.
        raw = bytes(message.data) if hasattr(message, "data") else message
        if isinstance(raw, str):
            raw = raw.encode()
        is_bank_route = model == "account.bank.ingestion"
        config = self.env["account.bank.ingestion.config"]
        if is_bank_route and custom_values and custom_values.get("config_id"):
            config = config.sudo().browse(custom_values["config_id"]).exists()
        if not is_bank_route:
            recipient_match = re.search(br"^(?:To|Delivered-To):\s*([^\r\n]+)", raw or b"", re.I | re.M)
            if recipient_match:
                recipients = recipient_match.group(1).decode("utf-8", "replace").lower()
                configs = self.env["account.bank.ingestion.config"].sudo().search([])
                config = configs.filtered(
                    lambda item: item.alias_full_name
                    and item.alias_full_name.lower() in recipients
                )[:1]
                is_bank_route = bool(config)
        if is_bank_route and config and raw:
            headers = BytesParser(policy=policy.default).parsebytes(
                raw, headersonly=True
            )
            message_id = (headers.get("Message-ID") or "").strip()
            if message_id:
                existing = self.env["account.bank.ingestion"].sudo().search(
                    [
                        ("config_id", "=", config.id),
                        ("message_id_header", "=", message_id),
                    ],
                    limit=1,
                )
                if existing:
                    existing.write(
                        {
                            "duplicate_delivery_count": existing.duplicate_delivery_count
                            + 1
                        }
                    )
                    return existing.id
        record_id = super().message_process(
            model,
            message,
            custom_values=custom_values,
            save_original=save_original,
            strip_attachments=strip_attachments,
            thread_id=thread_id,
        )
        if is_bank_route and record_id and raw:
            ingestion = self.env["account.bank.ingestion"].sudo().browse(
                record_id
            ).exists()
            if ingestion:
                self.env["ir.attachment"].sudo().create(
                    {
                        "name": "source-email.eml",
                        "raw": raw,
                        "mimetype": "message/rfc822",
                        "res_model": ingestion._name,
                        "res_id": ingestion.id,
                        "company_id": ingestion.company_id.id,
                    }
                )
        return record_id
