import datetime as dt
import ipaddress
import logging
import mimetypes
import re
import socket
import unicodedata
import urllib.parse
import urllib.request
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses, parseaddr
from pathlib import PurePosixPath
from urllib.error import HTTPError, URLError

from lxml import html
from psycopg2 import IntegrityError

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import format_date

from .bank_statement_review import REVIEW_STATES, is_accounting_operator
from odoo.addons.base.models.res_partner_bank import sanitize_account_number

_logger = logging.getLogger(__name__)

MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 100
MAX_COMPRESSION_RATIO = 100
MISSING_FITID_PREFIX = "__USL_MISSING_FITID_"
OFX_XML_COMPATIBILITY_HEADER = (
    b"OFXHEADER:200\n"
    b"DATA:OFXXML\n"
    b"VERSION:200\n"
    b"SECURITY:NONE\n"
    b"ENCODING:UTF-8\n"
    b"CHARSET:NONE\n"
    b"COMPRESSION:NONE\n\n"
)


def _split_config_values(value):
    return {
        item.strip().lower().rstrip(".")
        for item in re.split(r"[\s,;]+", value or "")
        if item.strip()
    }


def _month_end(value):
    next_month = (value.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
    return next_month - dt.timedelta(days=1)


def _ofx_account_matches(configured_identifier, parsed_identifier, ofx_account):
    """Match either a complete account identifier or strict French OFX parts."""
    configured = sanitize_account_number(configured_identifier or "").upper()
    parsed = sanitize_account_number(parsed_identifier or "").upper()
    if parsed == configured:
        return True
    if not configured.startswith("FR") or len(configured) != 27:
        return False
    expected_bank = configured[4:9]
    expected_branch = configured[9:14]
    expected_account = configured[14:25]
    return (
        sanitize_account_number(
            getattr(ofx_account, "routing_number", "") or "",
        ).upper()
        == expected_bank
        and sanitize_account_number(
            getattr(ofx_account, "branch_id", "") or "",
        ).upper()
        == expected_branch
        and sanitize_account_number(
            getattr(ofx_account, "account_id", "") or "",
        ).upper()
        == expected_account
    )


class AccountBankIngestionConfig(models.Model):
    _name = "account.bank.ingestion.config"
    _description = "Bank Statement Email Setup"
    _inherit = ["mail.alias.mixin", "mail.thread", "mail.activity.mixin"]
    _check_company_auto = True
    _order = "company_id, journal_id"

    name = fields.Char(required=True, tracking=True)
    active = fields.Boolean(default=True, tracking=True)
    processing_enabled = fields.Boolean(
        string="Receive and process emails",
        default=False,
        tracking=True,
        help=(
            "When enabled, Odoo accepts bank-export emails sent to this address, "
            "imports their OFX transactions, and saves the official PDF in Documents."
        ),
    )
    company_id = fields.Many2one(
        "res.company",
        required=True,
        default=lambda self: self.env.company,
        index=True,
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
    source_account_identifier = fields.Char(
        string="Bank account identifier",
        required=True,
        tracking=True,
        help="The IBAN or account number stated in the bank export.",
    )
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
    expected_delivery_day = fields.Integer(
        string="Expected by day",
        default=5,
        required=True,
        help="Day of the following month by which the bank email should arrive.",
    )
    ingestion_ids = fields.One2many("account.bank.ingestion", "config_id")
    statement_ids = fields.One2many("account.bank.statement", "ingestion_config_id")
    expected_period_start = fields.Date(compute="_compute_expected_review")
    expected_period_end = fields.Date(compute="_compute_expected_review")
    expected_delivery_date = fields.Date(compute="_compute_expected_review")
    review_status = fields.Selection(REVIEW_STATES, compute="_compute_expected_review")
    review_next_action = fields.Char(compute="_compute_expected_review")
    expected_statement_id = fields.Many2one(
        "account.bank.statement",
        compute="_compute_expected_review",
    )

    _active_journal_unique = models.UniqueIndex("(journal_id) WHERE active IS TRUE")

    @api.constrains("journal_id", "company_id")
    def _check_journal_company(self):
        for config in self:
            if config.journal_id.company_id != config.company_id:
                raise ValidationError(
                    _("The bank journal must belong to the configured company."),
                )
            bank_account = config.journal_id.bank_account_id
            if bank_account and sanitize_account_number(
                bank_account.account_number,
            ) != sanitize_account_number(config.source_account_identifier):
                raise ValidationError(
                    _("The source account must match the bank account on the journal."),
                )

    @api.constrains("responsible_user_id", "company_id")
    def _check_responsible_user(self):
        for config in self:
            if config.company_id not in config.responsible_user_id.company_ids:
                raise ValidationError(
                    _(
                        "The monthly review owner must have access to the configured company.",
                    ),
                )
            if not is_accounting_operator(config.responsible_user_id):
                raise ValidationError(
                    _(
                        "The monthly review owner must be an accountant who can complete the review.",
                    ),
                )

    @api.constrains("expected_delivery_day")
    def _check_delivery_day(self):
        if any(not 1 <= config.expected_delivery_day <= 28 for config in self):
            raise ValidationError(
                _("The expected delivery day must be between 1 and 28."),
            )

    @api.constrains("allowed_senders")
    def _check_allowed_senders(self):
        for config in self:
            for sender in config._allowed_sender_set():
                if parseaddr(sender)[1].lower() != sender or "@" not in sender:
                    raise ValidationError(
                        _("Use complete, exact sender email addresses."),
                    )

    @api.constrains("allowed_download_hosts")
    def _check_allowed_hosts(self):
        for config in self:
            for host in config._allowed_host_set():
                if "/" in host or ":" in host or not re.fullmatch(r"[a-z0-9.-]+", host):
                    raise ValidationError(
                        _("Download hosts must be plain DNS host names."),
                    )

    @api.constrains(
        "processing_enabled",
        "alias_name",
        "alias_domain_id",
        "allowed_senders",
        "allowed_download_hosts",
    )
    def _check_email_processing_readiness(self):
        for config in self.filtered("processing_enabled"):
            if not config.alias_name or not config.alias_domain_id:
                raise ValidationError(
                    _(
                        "Set the complete 'Send bank exports to' email address "
                        "before enabling email processing.",
                    ),
                )
            if not config._allowed_sender_set():
                raise ValidationError(
                    _(
                        "Add at least one accepted sender address before enabling "
                        "email processing.",
                    ),
                )
            if config.provider == "shine" and not config._allowed_host_set():
                raise ValidationError(
                    _(
                        "Add Shine's accounting download site before enabling email "
                        "processing.",
                    ),
                )

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
                "alias_model_id": self.env["ir.model"]._get_id(
                    "account.bank.ingestion",
                ),
                "alias_contact": "everyone",
                "alias_defaults": repr({"config_id": self.id}),
            },
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
        last_completed = today.replace(day=1) - dt.timedelta(days=1)
        for config in self:
            exceptional_statement = config.statement_ids.filtered(
                lambda item: item.unresolved_exception_count,
            ).sorted(lambda item: (item.period_start or today, item.id))[:1]
            if exceptional_statement:
                config.expected_period_start = exceptional_statement.period_start
                config.expected_period_end = exceptional_statement.period_end
                config.expected_delivery_date = (
                    exceptional_statement.period_end
                    + dt.timedelta(days=config.expected_delivery_day)
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
                    lambda item: (
                        item.period_start == period_start
                        and item.period_end == period_end
                    ),
                )[:1]
                if not statement or statement.certification_state != "certified":
                    break
                period_start = period_end + dt.timedelta(days=1)
            if period_start > last_completed:
                period_start = last_completed.replace(day=1)
                statement = config.statement_ids.filtered(
                    lambda item: item.period_start == period_start,
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
                config.review_next_action = statement.review_blocking_reason or _(
                    "Open the statement review.",
                )
            else:
                ingestions = config.ingestion_ids.filtered(
                    lambda item: (
                        item.period_start == period_start
                        and item.period_end == period_end
                    ),
                )
                if any(item.state in ("received", "processing") for item in ingestions):
                    config.review_status = "processing"
                    config.review_next_action = _(
                        "The received bank export is being processed.",
                    )
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
                "view_id": self.env.ref(
                    "usl_accounting.view_bank_statement_form_review",
                ).id,
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
        activity_type = self.env.ref(
            "usl_accounting.mail_activity_type_bank_statement_review",
        )
        summary = _("Review scheduled bank statement")
        activities = self.env["mail.activity"].search(
            [
                ("res_model_id", "=", model_id),
                ("res_id", "=", self.journal_id.id),
                ("activity_type_id", "=", activity_type.id),
            ],
        )
        needs_activity = self.review_status == "attention" or (
            self.review_status == "expected"
            and fields.Date.context_today(self) > self.expected_delivery_date
        )
        if needs_activity and not activities:
            self.journal_id.activity_schedule(
                "usl_accounting.mail_activity_type_bank_statement_review",
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
        "account.bank.ingestion.config",
        compute="_compute_bank_ingestion_review",
    )
    bank_statement_review_status = fields.Selection(
        REVIEW_STATES,
        compute="_compute_bank_ingestion_review",
    )
    bank_statement_review_period = fields.Char(compute="_compute_bank_ingestion_review")
    bank_statement_review_next_action = fields.Char(
        compute="_compute_bank_ingestion_review",
    )

    def _compute_bank_ingestion_review(self):
        configs = self.env["account.bank.ingestion.config"].search(
            [("journal_id", "in", self.ids), ("active", "=", True)],
        )
        by_journal = {config.journal_id.id: config for config in configs}
        for journal in self:
            config = by_journal.get(journal.id)
            journal.bank_ingestion_config_id = config
            journal.bank_statement_review_status = (
                config.review_status if config else False
            )
            journal.bank_statement_review_period = (
                format_date(self.env, config.expected_period_start, date_format="MMM y")
                if config and config.expected_period_start
                else False
            )
            journal.bank_statement_review_next_action = (
                config.review_next_action if config else False
            )

    def action_open_bank_statement_review(self):
        self.ensure_one()
        if not self.bank_ingestion_config_id:
            raise UserError(
                _("No scheduled bank export route is configured for this journal."),
            )
        return self.bank_ingestion_config_id.action_open_expected_statement()


class AccountBankIngestion(models.Model):
    _name = "account.bank.ingestion"
    _description = "Received Bank Export"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _check_company_auto = True
    _order = "received_at desc, id desc"

    name = fields.Char(required=True, tracking=True)
    config_id = fields.Many2one(
        "account.bank.ingestion.config",
        required=True,
        ondelete="restrict",
        check_company=True,
        index=True,
    )
    company_id = fields.Many2one(
        "res.company",
        related="config_id.company_id",
        store=True,
        index=True,
    )
    journal_id = fields.Many2one(
        "account.journal",
        related="config_id.journal_id",
        store=True,
        check_company=True,
        index=True,
    )
    message_id_header = fields.Char(string="Source Message-ID", copy=False, index=True)
    sender = fields.Char(copy=False)
    recipient = fields.Char(copy=False)
    subject = fields.Char(copy=False)
    headers = fields.Text(copy=False, groups="account.group_account_manager")
    body_html = fields.Html(
        copy=False,
        sanitize=False,
        groups="account.group_account_manager",
    )
    received_at = fields.Datetime(
        default=fields.Datetime.now,
        required=True,
        copy=False,
    )
    period_start = fields.Date(copy=False, index=True)
    period_end = fields.Date(copy=False, index=True)
    state = fields.Selection(
        [
            ("received", "Received"),
            ("processing", "Processing"),
            ("done", "Processed"),
            ("attention", "Needs attention"),
            ("failed", "Import failed"),
        ],
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
    exception_ids = fields.One2many(
        "account.bank.statement.exception",
        "ingestion_id",
        readonly=True,
    )
    statement_ids = fields.Many2many(
        "account.bank.statement",
        compute="_compute_statements",
    )
    unresolved_exception_count = fields.Integer(compute="_compute_statements")

    _message_config_unique = models.UniqueIndex(
        "(config_id, message_id_header) WHERE message_id_header IS NOT NULL",
    )

    @api.depends(
        "file_ids.statement_id",
        "exception_ids.statement_id",
        "exception_ids.state",
    )
    def _compute_statements(self):
        for ingestion in self:
            ingestion.statement_ids = (
                ingestion.file_ids.statement_id | ingestion.exception_ids.statement_id
            )
            ingestion.unresolved_exception_count = len(
                ingestion.exception_ids.filtered(lambda item: item.state == "open"),
            )

    @api.model
    def message_new(self, msg_dict, custom_values=None):
        values = dict(custom_values or {})
        config = (
            self.env["account.bank.ingestion.config"]
            .browse(values.get("config_id"))
            .exists()
        )
        if not config:
            raise ValidationError(_("The bank export email route is not configured."))
        sender = parseaddr(msg_dict.get("email_from") or "")[1].strip().lower()
        period_start, period_end = self._period_from_text(msg_dict.get("subject") or "")
        message_id = (msg_dict.get("message_id") or "").strip() or False
        existing = message_id and self.sudo().search(
            [("config_id", "=", config.id), ("message_id_header", "=", message_id)],
            limit=1,
        )
        if existing:
            existing.sudo().write(
                {"duplicate_delivery_count": existing.duplicate_delivery_count + 1},
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
            },
        )
        try:
            with self.env.cr.savepoint():
                return self.create(values)
        except IntegrityError:
            if not message_id:
                raise
            existing = self.sudo().search(
                [("config_id", "=", config.id), ("message_id_header", "=", message_id)],
                limit=1,
            )
            if not existing:
                raise
            existing.sudo().write(
                {"duplicate_delivery_count": existing.duplicate_delivery_count + 1},
            )
            return existing

    @api.model
    def _period_from_text(self, value):
        matches = re.findall(r"(\d{2})[/-](\d{2})[/-](\d{4})", value or "")
        if len(matches) < 2:
            normalized = "".join(
                char for char in unicodedata.normalize("NFKD", (value or "").casefold())
                if not unicodedata.combining(char)
            )
            months = ["janvier", "fevrier", "mars", "avril", "mai", "juin", "juillet", "aout", "septembre", "octobre", "novembre", "decembre"]
            periods = re.findall(r"\b(" + "|".join(months) + r")\s+(\d{4})\b", normalized)
            if not matches and len(periods) == 1:
                month, year = periods[0]
                try:
                    start = dt.date(int(year), months.index(month) + 1, 1)
                    return start, _month_end(start)
                except ValueError:
                    pass
            return False, False
        try:
            dates = [
                dt.date(int(year), int(month), int(day))
                for day, month, year in matches[:2]
            ]
        except ValueError:
            return False, False
        return min(dates), max(dates)

    def action_correct_period(self):
        self.ensure_one()
        if not is_accounting_operator(self.env.user):
            raise AccessError(_("Only an accountant can correct a bank export period."))
        self.check_access("read")
        if self.state not in ("attention", "failed"):
            raise UserError(_("Only an export needing attention can be corrected."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Correct statement period"),
            "res_model": "account.bank.ingestion.period",
            "view_mode": "form",
            "target": "new",
            "context": {"default_ingestion_id": self.id,
                        "default_period_start": self.period_start,
                        "default_period_end": self.period_end},
        }

    def _verified_file_periods(self, pdf=None):
        self.ensure_one()
        verified = self.file_ids.filtered(
            lambda source: source.classification == "ofx"
            and source.processing_state in ("processed", "duplicate")
            and source.statement_id,
        )
        periods = {(source.period_start, source.period_end) for source in verified}
        pdfs = pdf if pdf is not None else self.file_ids.filtered(lambda source: source.classification == "pdf")
        for candidate in pdfs:
            accepted = self.env["account.bank.ingestion.file"].search([
                ("sha256", "=", candidate.sha256),
                ("evidence_status", "=", "accepted"),
                ("ingestion_id.config_id", "=", self.config_id.id),
                ("company_id", "=", self.company_id.id),
            ])
            for source in accepted:
                if source.statement_id.accepted_evidence_id == source:
                    periods.add((source.statement_id.period_start, source.statement_id.period_end))
        return periods

    def action_process_now(self):
        if not is_accounting_operator(self.env.user):
            raise AccessError(
                _("Only an accountant can process a received bank export."),
            )
        for ingestion in self:
            ingestion.sudo()._process()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "message": _("Bank export processing finished."),
                "type": "warning"
                if any(item.state in ("attention", "failed") for item in self)
                else "success",
                "sticky": False,
                "next": {"type": "ir.actions.client", "tag": "reload"},
            },
        }

    def action_retry(self):
        return self.action_process_now()

    def action_open_add_source_file(self):
        self.ensure_one()
        if not is_accounting_operator(self.env.user):
            raise AccessError(_("Only an accountant can add a missing bank export file."))
        self.check_access("read")
        return {
            "type": "ir.actions.act_window",
            "name": _("Add a missing bank export file"),
            "res_model": "account.bank.ingestion.upload",
            "view_mode": "form",
            "target": "new",
            "context": {"default_ingestion_id": self.id},
        }

    @api.model
    def _cron_process_pending(self, limit=10):
        self.env.cr.execute(
            """
            SELECT id
             FROM account_bank_ingestion
             WHERE state = 'received'
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
        if not self.config_id.processing_enabled and self.env.context.get(
            "bank_ingestion_cron",
        ):
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
            },
        )
        try:
            self._retain_message_attachments()
            self._validate_sender()
            self._download_configured_links()
            self._extract_archives()
            files = self.file_ids.filtered(
                lambda item: item.classification not in ("email", "zip"),
            )
            ofx_files = files.filtered(lambda item: item.classification == "ofx")
            for source_file in ofx_files:
                source_file._process_isolated()
            for source_file in files.filtered(
                lambda item: item.classification == "pdf",
            ):
                source_file._process_isolated()
            self._finalize_retained_files()
            missing_ofx_name = _("OFX transaction export missing")
            matched_statements = files.filtered(
                lambda item: item.classification == "pdf",
            ).statement_id
            transactions_already_present = any(
                statement.line_ids and not statement.unidentified_line_count
                for statement in matched_statements
            )
            missing_ofx_exceptions = self.exception_ids.filtered(
                lambda item: (
                    item.kind == "import"
                    and item.name == missing_ofx_name
                    and item.state == "open"
                ),
            )
            if not ofx_files and not transactions_already_present:
                self._ensure_exception(
                    "import",
                    missing_ofx_name,
                    _(
                        "No OFX transaction export was found. Add the OFX file or a recovered ZIP, then retry.",
                    ),
                )
            elif missing_ofx_exceptions:
                missing_ofx_exceptions.sudo().with_context(
                    bank_exception_internal=True,
                ).write(
                    {
                        "state": "resolved",
                        "resolution": "corrected_source",
                        "resolution_reason": _(
                            "The matched statement already contains its identified bank transactions.",
                        ),
                        "resolved_by_id": self.env.user.id,
                        "resolved_at": fields.Datetime.now(),
                    },
                )
            self._resolve_recovered_import_failures(files)
            open_exceptions = self.exception_ids.filtered(
                lambda item: item.state == "open",
            )
            self.state = "attention" if open_exceptions else "done"
            self.message_post(
                body=(
                    _("Bank export retained and processed; review is required.")
                    if open_exceptions
                    else _("Bank export retained and processed successfully.")
                ),
            )
        # A received source must survive unexpected parser and attachment errors.
        except Exception as error:  # noqa: BLE001
            _logger.info(
                "Bank export processing failed for ingestion %s: %s",
                self.id,
                type(error).__name__,
            )
            self.write({"state": "failed", "last_error": str(error)})
            self._fail_pending_files(error)
            self._ensure_exception(
                "import",
                _("Bank export processing failed"),
                str(error),
            )
        finally:
            self.config_id._sync_review_activity()

    def _finalize_retained_files(self):
        """Give every retained file a durable, explicit disposition."""
        for ingestion in self.sorted("id"):
            pending = ingestion.file_ids.filtered(
                lambda item: item.processing_state == "pending",
            ).sorted("id")
            for source_file in pending:
                if source_file.classification == "email":
                    source_file.sudo().write(
                        {
                            "processing_state": "processed",
                            "processing_detail": _(
                                "Original source email retained unchanged.",
                            ),
                        },
                    )
                    continue
                if source_file.classification in ("csv", "qif", "unsupported"):
                    source_file._finalize_supplemental_file()
                    continue
                detail = _(
                    "The retained file has no completed processing result. Retry the bank export; if it fails again, replace the file with a fresh bank export.",
                )
                source_file.sudo().write(
                    {
                        "processing_state": "failed",
                        "processing_detail": detail,
                    },
                )
                source_file._ensure_exception(
                    "import",
                    _("Retained file was not fully processed"),
                    detail,
                )

    def _fail_pending_files(self, error):
        detail = _(
            "The bank email could not be fully processed: %(error)s Retry after correcting the reported problem.",
            error=str(error),
        )
        for source_file in self.file_ids.filtered(
            lambda item: item.processing_state == "pending",
        ):
            source_file.sudo().write(
                {
                    "processing_state": "failed",
                    "processing_detail": detail,
                },
            )

    def _resolve_recovered_import_failures(self, files):
        self.ensure_one()
        recovered_ofx = files.filtered(
            lambda item: (
                item.recovered_upload
                and item.classification == "ofx"
                and item.processing_state in ("processed", "duplicate")
                and item.statement_id
            ),
        )
        if not recovered_ofx:
            return
        recovered_statements = recovered_ofx.statement_id
        corrected = self.exception_ids.filtered(
            lambda item: (
                item.state == "open"
                and item.kind == "import"
                and item.file_id not in recovered_ofx
                and (not item.file_id or item.file_id.classification in ("ofx", "zip"))
                and (not item.statement_id or item.statement_id in recovered_statements)
            ),
        )
        if not corrected:
            return
        statement = recovered_statements[:1]
        corrected.sudo().with_context(bank_exception_internal=True).write(
            {
                "statement_id": statement.id,
                "state": "resolved",
                "resolution": "corrected_source",
                "resolution_reason": _(
                    "A retained recovered export supplied the missing transactions.",
                ),
                "resolved_by_id": self.env.user.id,
                "resolved_at": fields.Datetime.now(),
            },
        )

    def _refresh_processing_state(self):
        for ingestion in self:
            ingestion.sudo().state = (
                "attention"
                if ingestion.exception_ids.filtered(lambda item: item.state == "open")
                else "done"
            )
            ingestion.config_id._sync_review_activity()

    def _validate_sender(self):
        self.ensure_one()
        if self.sender not in self.config_id._allowed_sender_set():
            raise UserError(
                _(
                    "The sender %(sender)s is not approved for this bank export route.",
                    sender=self.sender or _("unknown"),
                ),
            )

    def _retain_message_attachments(self):
        Attachment = self.env["ir.attachment"].sudo()
        attachments = Attachment.search(
            [("res_model", "=", self._name), ("res_id", "=", self.id)],
        )
        for attachment in attachments:
            if not attachment.raw:
                continue
            self.env["account.bank.ingestion.file"]._from_attachment(self, attachment)

    def _download_configured_links(self):
        self.ensure_one()
        if not self.body_html or self.file_ids.filtered(
            lambda item: item.classification in ("zip", "ofx"),
        ):
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
            if (
                parsed.scheme != "https"
                or host not in allowed_hosts
                or host in existing_hosts
            ):
                continue
            try:
                content, filename, mimetype = self._download_https(href, host)
            except (HTTPError, URLError, TimeoutError, OSError) as error:
                raise UserError(
                    _(
                        "The scheduled export link could not be downloaded. It may have expired; attach a freshly downloaded export and retry.",
                    ),
                ) from error
            attachment = (
                self.env["ir.attachment"]
                .sudo()
                .create(
                    {
                        "name": filename,
                        "raw": content,
                        "mimetype": mimetype,
                        "res_model": self._name,
                        "res_id": self.id,
                        "company_id": self.company_id.id,
                    },
                )
            )
            self.env["account.bank.ingestion.file"]._from_attachment(
                self,
                attachment,
                download_host=host,
            )
            existing_hosts.add(host)

    def _download_https(self, url, expected_host):
        self._validate_public_host(expected_host)

        class SameHostRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(
                handler_self,
                request,
                fp,
                code,
                message,
                headers,
                new_url,
            ):
                redirected = urllib.parse.urlsplit(new_url)
                if (
                    redirected.scheme != "https"
                    or (redirected.hostname or "").lower().rstrip(".") != expected_host
                ):
                    raise UserError(
                        _("The bank export download redirected to an unapproved host."),
                    )
                return super().redirect_request(
                    request,
                    fp,
                    code,
                    message,
                    headers,
                    new_url,
                )

        request = urllib.request.Request(
            url,
            headers={"User-Agent": "USL-Odoo-Bank-Export/1"},
        )
        with urllib.request.build_opener(SameHostRedirect()).open(
            request,
            timeout=20,
        ) as response:
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
                    raise UserError(
                        _("The bank export download exceeds the 50 MiB limit."),
                    )
            disposition = response.headers.get("Content-Disposition") or ""
            filename_match = re.search(
                r"filename\*?=(?:UTF-8''|\")?([^\";]+)",
                disposition,
                re.I,
            )
            filename = (
                urllib.parse.unquote(filename_match.group(1))
                if filename_match
                else PurePosixPath(urllib.parse.urlsplit(url).path).name
            )
            filename = PurePosixPath(filename or "bank-export.zip").name
            mimetype = (
                response.headers.get_content_type() or mimetypes.guess_type(filename)[0]
            )
            return b"".join(chunks), filename, mimetype or "application/octet-stream"

    def _validate_public_host(self, host):
        for info in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM):
            address = ipaddress.ip_address(info[4][0])
            if not address.is_global:
                raise UserError(
                    _(
                        "The configured download host does not resolve to a public address.",
                    ),
                )

    def _extract_archives(self):
        for archive in self.file_ids.filtered(
            lambda item: (
                item.classification == "zip" and item.processing_state == "pending"
            ),
        ):
            archive._extract_zip()

    def _ensure_exception(self, kind, name, detail, file=False, statement=False):
        if not statement:
            linked_statements = self.file_ids.statement_id
            statement = linked_statements if len(linked_statements) == 1 else False
        if not statement and self.period_start and self.period_end:
            statement = (
                self.env["account.bank.statement"]
                .sudo()
                .search(
                    [
                        ("ingestion_config_id", "=", self.config_id.id),
                        ("period_start", "=", self.period_start),
                        ("period_end", "=", self.period_end),
                    ],
                    limit=1,
                )
            )
        ExceptionModel = self.env["account.bank.statement.exception"].sudo()
        existing = ExceptionModel.search(
            [
                ("ingestion_id", "=", self.id),
                ("file_id", "=", file.id if file else False),
                ("kind", "=", kind),
                ("name", "=", name),
                ("state", "=", "open"),
            ],
            limit=1,
        )
        if existing:
            if statement and not existing.statement_id:
                existing.with_context(bank_exception_internal=True).write(
                    {"statement_id": statement.id},
                )
            return existing
        return ExceptionModel.create(
            {
                "ingestion_id": self.id,
                "file_id": file.id if file else False,
                "statement_id": statement.id if statement else False,
                "company_id": self.company_id.id,
                "kind": kind,
                "name": name,
                "detail": detail,
            },
        )


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
            parsed_headers = BytesParser(policy=policy.default).parsebytes(
                raw or b"",
                headersonly=True,
            )
            recipient_headers = []
            for header_name in ("To", "Cc", "Delivered-To", "X-Original-To"):
                recipient_headers.extend(parsed_headers.get_all(header_name, []))
            recipients = {
                address.lower() for _name, address in getaddresses(recipient_headers)
            }
            if recipients:
                configs = self.env["account.bank.ingestion.config"].sudo().search([])
                config = configs.filtered(
                    lambda item: (
                        item.alias_full_name
                        and item.alias_full_name.lower() in recipients
                    ),
                )[:1]
                is_bank_route = bool(config)
        if is_bank_route and config and raw:
            headers = BytesParser(policy=policy.default).parsebytes(
                raw,
                headersonly=True,
            )
            message_id = (headers.get("Message-ID") or "").strip()
            if message_id:
                existing = (
                    self.env["account.bank.ingestion"]
                    .sudo()
                    .search(
                        [
                            ("config_id", "=", config.id),
                            ("message_id_header", "=", message_id),
                        ],
                        limit=1,
                    )
                )
                if existing:
                    existing.write(
                        {
                            "duplicate_delivery_count": existing.duplicate_delivery_count
                            + 1,
                        },
                    )
                    return existing.with_env(self.env)
        record = super().message_process(
            model,
            message,
            custom_values=custom_values,
            save_original=save_original,
            strip_attachments=strip_attachments,
            thread_id=thread_id,
        )
        if is_bank_route and record and raw:
            ingestion = record.sudo().exists()
            if ingestion:
                self.env["ir.attachment"].sudo().create(
                    {
                        "name": "source-email.eml",
                        "raw": raw,
                        "mimetype": "message/rfc822",
                        "res_model": ingestion._name,
                        "res_id": ingestion.id,
                        "company_id": ingestion.company_id.id,
                    },
                )
                if ingestion.config_id.processing_enabled and ingestion.state == "received":
                    ingestion.with_context(bank_ingestion_cron=True)._process()
        return record
