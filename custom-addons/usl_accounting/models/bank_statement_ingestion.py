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
from email.utils import getaddresses, parseaddr
from io import BytesIO
from pathlib import PurePosixPath
from urllib.error import HTTPError, URLError

from lxml import html
from psycopg2 import IntegrityError

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import BinaryBytes, format_date
from odoo.tools.pdf import PdfReader

from .bank_statement_review import REVIEW_STATES, is_accounting_operator
from odoo.addons.base.models.res_partner_bank import sanitize_account_number

_logger = logging.getLogger(__name__)

MAX_DOWNLOAD_BYTES = 50 * 1024 * 1024
MAX_ARCHIVE_BYTES = 100 * 1024 * 1024
MAX_ARCHIVE_MEMBERS = 100
MAX_COMPRESSION_RATIO = 100
MISSING_FITID_PREFIX = "__USL_MISSING_FITID_"


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
            return False, False
        try:
            dates = [
                dt.date(int(year), int(month), int(day))
                for day, month, year in matches[:2]
            ]
        except ValueError:
            return False, False
        return min(dates), max(dates)

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
            for source_file in files.filtered(
                lambda item: item.classification == "unsupported",
            ):
                source_file._ensure_exception(
                    "unsupported",
                    _("Unsupported bank export attachment"),
                    _(
                        "The attachment %(name)s needs an accounting review.",
                        name=source_file.filename,
                    ),
                )
                source_file.processing_state = "attention"
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
            self._ensure_exception(
                "import",
                _("Bank export processing failed"),
                str(error),
            )
        finally:
            self.config_id._sync_review_activity()

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


class AccountBankIngestionFile(models.Model):
    _name = "account.bank.ingestion.file"
    _description = "Retained Bank Export File"
    _order = "ingestion_id desc, id"
    _check_company_auto = True

    ingestion_id = fields.Many2one(
        "account.bank.ingestion",
        required=True,
        ondelete="restrict",
        check_company=True,
        index=True,
    )
    company_id = fields.Many2one(
        "res.company",
        related="ingestion_id.company_id",
        store=True,
        index=True,
    )
    attachment_id = fields.Many2one(
        "ir.attachment",
        required=True,
        ondelete="restrict",
        copy=False,
    )
    filename = fields.Char(required=True, copy=False)
    mimetype = fields.Char(copy=False)
    sha256 = fields.Char(required=True, copy=False, index=True)
    size = fields.Integer(required=True, copy=False)
    classification = fields.Selection(
        [
            ("email", "Source email"),
            ("zip", "Original export archive"),
            ("ofx", "OFX transactions"),
            ("pdf", "Official bank statement"),
            ("csv", "CSV copy"),
            ("qif", "QIF copy"),
            ("unsupported", "Needs review"),
        ],
        required=True,
        index=True,
    )
    processing_state = fields.Selection(
        [
            ("pending", "Pending"),
            ("processed", "Processed"),
            ("duplicate", "Already imported"),
            ("attention", "Needs attention"),
            ("failed", "Failed"),
        ],
        default="pending",
        required=True,
        index=True,
    )
    processing_detail = fields.Text(copy=False)
    download_host = fields.Char(copy=False, groups="account.group_account_manager")
    recovered_upload = fields.Boolean(copy=False, readonly=True)
    parent_archive_id = fields.Many2one(
        "account.bank.ingestion.file",
        ondelete="restrict",
        check_company=True,
    )
    extracted_file_ids = fields.One2many(
        "account.bank.ingestion.file",
        "parent_archive_id",
        readonly=True,
    )
    statement_id = fields.Many2one(
        "account.bank.statement",
        ondelete="restrict",
        check_company=True,
        index=True,
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
    parsed_balance_start = fields.Monetary(
        string="Export opening balance",
        currency_field="currency_id",
        copy=False,
    )
    parsed_balance_end_real = fields.Monetary(
        string="Export closing balance",
        currency_field="currency_id",
        copy=False,
    )
    currency_id = fields.Many2one(
        "res.currency",
        compute="_compute_currency",
        readonly=True,
    )
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
        "(statement_id) WHERE evidence_status = 'accepted'",
    )

    @api.depends(
        "ingestion_id.journal_id.currency_id",
        "ingestion_id.company_id.currency_id",
    )
    def _compute_currency(self):
        for source_file in self:
            source_file.currency_id = (
                source_file.ingestion_id.journal_id.currency_id
                or source_file.ingestion_id.company_id.currency_id
            )

    @api.model
    def _from_attachment(
        self,
        ingestion,
        attachment,
        download_host=False,
        parent_archive=False,
        forced_classification=False,
        recovered_upload=False,
    ):
        existing = self.search([("attachment_id", "=", attachment.id)], limit=1)
        if existing:
            return existing
        if attachment.company_id and attachment.company_id != ingestion.company_id:
            raise ValidationError(
                _("The source attachment belongs to another company."),
            )
        if not attachment.company_id:
            attachment.sudo().company_id = ingestion.company_id
        content = bytes(attachment.raw or b"")
        return self.create(
            {
                "ingestion_id": ingestion.id,
                "attachment_id": attachment.id,
                "filename": attachment.name or _("Unnamed attachment"),
                "mimetype": attachment.mimetype,
                "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content),
                "classification": forced_classification
                or self._classify(attachment.name, attachment.mimetype, content),
                "download_host": download_host,
                "parent_archive_id": parent_archive.id if parent_archive else False,
                "recovered_upload": recovered_upload
                or bool(parent_archive and parent_archive.recovered_upload),
            },
        )

    @api.model
    def _classify(self, filename, mimetype, content):
        lower = (filename or "").lower()
        if lower.endswith(".eml") or (mimetype or "").lower() in (
            "message/rfc822",
            "text/rfc822-headers",
        ):
            return "email"
        if content.startswith(b"PK\x03\x04") and lower.endswith(".zip"):
            return "zip"
        if content.startswith(b"%PDF-") and lower.endswith(".pdf"):
            return "pdf"
        sample = content[:4096].lstrip().upper()
        if lower.endswith(".ofx") and (
            b"<OFX" in sample or sample.startswith(b"OFXHEADER:")
        ):
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
            raise UserError(
                _("The retained source file no longer matches its recorded checksum."),
            )
        return content

    @api.model
    def _pdf_integrity_error(self, content):
        """Return user-facing guidance when a retained PDF cannot be opened."""
        damaged_message = _(
            "The received PDF is damaged or incomplete. Replace it with the "
            "original PDF downloaded from the bank.",
        )
        try:
            reader = PdfReader(BytesIO(content), strict=False)
            if reader.is_encrypted and not reader.decrypt(""):
                return _(
                    "The received PDF is password-protected. Replace it with an "
                    "unlocked original PDF from the bank.",
                )
            if not len(reader.pages):
                return damaged_message
        except Exception:  # noqa: BLE001 - third-party PDF readers raise varied errors
            return damaged_message
        return False

    def _extract_zip(self):
        self.ensure_one()
        content = self._content()
        try:
            archive = zipfile.ZipFile(BytesIO(content))
        except zipfile.BadZipFile as error:
            self.write(
                {
                    "processing_state": "failed",
                    "processing_detail": _("The export archive is malformed."),
                },
            )
            self._ensure_exception("import", _("Malformed export archive"), str(error))
            return
        members = archive.infolist()
        if len(members) > MAX_ARCHIVE_MEMBERS:
            raise UserError(_("The export archive contains too many files."))
        total = 0
        for member in members:
            path = PurePosixPath(member.filename)
            mode = member.external_attr >> 16
            if (
                path.is_absolute()
                or ".." in path.parts
                or (mode & 0o170000) == 0o120000
            ):
                raise UserError(
                    _("The export archive contains an unsafe path or link."),
                )
            if member.is_dir():
                continue
            total += member.file_size
            if total > MAX_ARCHIVE_BYTES:
                raise UserError(_("The uncompressed export exceeds the 100 MiB limit."))
            if (
                member.compress_size
                and member.file_size / member.compress_size > MAX_COMPRESSION_RATIO
            ):
                raise UserError(
                    _("The export archive contains an unsafe compression ratio."),
                )
            member_content = archive.read(member)
            filename = path.name
            nested_kind = self._classify(
                filename,
                mimetypes.guess_type(filename)[0],
                member_content,
            )
            if nested_kind == "zip":
                nested_kind = "unsupported"
            attachment = (
                self.env["ir.attachment"]
                .sudo()
                .create(
                    {
                        "name": filename,
                        "raw": member_content,
                        "mimetype": mimetypes.guess_type(filename)[0]
                        or "application/octet-stream",
                        "res_model": self.ingestion_id._name,
                        "res_id": self.ingestion_id.id,
                        "company_id": self.company_id.id,
                    },
                )
            )
            self._from_attachment(
                self.ingestion_id,
                attachment,
                parent_archive=self,
                forced_classification=nested_kind,
                recovered_upload=self.recovered_upload,
            )
        self.write(
            {
                "processing_state": "processed",
                "processing_detail": _("Archive retained and safely extracted."),
            },
        )

    def _process_isolated(self):
        self.ensure_one()
        try:
            with self.env.cr.savepoint():
                if self.classification == "ofx":
                    self._process_ofx()
                elif self.classification == "pdf":
                    self._associate_pdf()
        # The per-file savepoint deliberately isolates third-party parser failures.
        except Exception as error:  # noqa: BLE001
            self.write({"processing_state": "failed", "processing_detail": str(error)})
            self._ensure_exception(
                "import",
                _("Attachment processing failed"),
                str(error),
            )

    def _process_ofx(self):
        self.ensure_one()
        content = self._content()
        parser_content = self._with_parser_fitid_placeholders(content)
        wizard = (
            self.env["account.statement.import"]
            .with_context(journal_id=self.ingestion_id.journal_id.id)
            .create(
                {
                    "statement_file": BinaryBytes(parser_content),
                    "statement_filename": self.filename,
                },
            )
        )
        ofx = wizard._check_ofx(parser_content)
        if not ofx:
            raise UserError(_("The OFX attachment is malformed or unsupported."))
        parsed_accounts = wizard._parse_file(parser_content)
        if len(parsed_accounts) != 1 or len(ofx.accounts) != 1:
            raise UserError(_("The bank export must contain exactly one bank account."))
        currency_code, account_number, statements_values = parsed_accounts[0]
        config = self.ingestion_id.config_id
        ofx_account = ofx.accounts[0]
        if not _ofx_account_matches(
            config.source_account_identifier,
            account_number,
            ofx_account,
        ):
            self._ensure_exception(
                "account",
                _("Bank account does not match"),
                _(
                    "The OFX account does not match the account configured for this route.",
                ),
            )
            self.processing_state = "attention"
            return
        canonical_account_id = sanitize_account_number(
            config.source_account_identifier,
        )
        self.exception_ids.filtered(
            lambda item: item.kind == "account" and item.state == "open",
        ).sudo().with_context(bank_exception_internal=True).write(
            {
                "state": "resolved",
                "resolution": "corrected_source",
                "resolution_reason": _(
                    "The retained OFX account components match the configured bank account.",
                ),
                "resolved_by_id": self.env.user.id,
                "resolved_at": fields.Datetime.now(),
            },
        )
        currency = wizard._match_currency(currency_code)
        journal_currency = (
            config.journal_id.currency_id or config.company_id.currency_id
        )
        if currency != journal_currency:
            raise UserError(
                _("The OFX currency does not match the configured bank journal."),
            )
        if len(statements_values) != 1:
            raise UserError(
                _("The OFX export contains an ambiguous statement population."),
            )
        values = wizard._complete_stmts_vals(
            statements_values,
            config.journal_id,
            account_number,
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
            raise UserError(
                _("This export predates the configured ingestion cut-over."),
            )
        self.env.cr.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            [f"account.bank.period:{config.id}:{period_start.isoformat()}"],
        )
        self.write(
            {
                "period_start": period_start,
                "period_end": period_end,
                "parsed_balance_start": values.get("balance_start", 0),
                "parsed_balance_end_real": values.get("balance_end_real", 0),
            },
        )
        raw_ids = [
            ""
            if item.id is None or str(item.id).startswith(MISSING_FITID_PREFIX)
            else str(item.id).strip()
            for item in raw_transactions
        ]
        duplicate_ids = {item for item in raw_ids if item and raw_ids.count(item) > 1}
        new_values = []
        existing_lines = self.env["account.bank.statement.line"]
        existing_provider_ids = {}
        ambiguous = []
        for ordinal, (line_values, raw_id) in enumerate(
            zip(transactions, raw_ids),
            start=1,
        ):
            line_values = dict(line_values)
            line_values["date"] = fields.Date.to_date(line_values["date"])
            if not raw_id or raw_id in duplicate_ids:
                fallback = hashlib.sha256(
                    f"{self.sha256}:{canonical_account_id}:{period_start}:{ordinal}".encode(),
                ).hexdigest()
                candidate = {
                    **line_values,
                    "date": line_values["date"].isoformat(),
                    "unique_import_id": f"fallback-{fallback}",
                    "provider_code": config.provider,
                    "provider_account_id": canonical_account_id,
                    "provider_transaction_id": f"fallback:{fallback}",
                }
                prior_decision = self.env["account.bank.statement.exception"].search(
                    [
                        ("file_id", "=", self.id),
                        ("kind", "=", "identity"),
                        (
                            "name",
                            "=",
                            _(
                                "Transaction %(ordinal)s needs an identity decision",
                                ordinal=ordinal,
                            ),
                        ),
                        ("state", "=", "resolved"),
                        ("mapped_line_id", "!=", False),
                    ],
                    order="id desc",
                    limit=1,
                )
                if prior_decision:
                    prior_decision._validate_candidate_mapping(
                        prior_decision.mapped_line_id,
                        candidate,
                    )
                    existing_lines |= prior_decision.mapped_line_id
                    continue
                ambiguous.append((ordinal, raw_id, candidate))
                continue
            existing = self._find_existing_transaction(
                config,
                raw_id,
                line_values["unique_import_id"],
                line_values["date"],
                line_values["amount"],
            )
            if existing:
                if (
                    existing.currency_id.compare_amounts(
                        existing.amount,
                        line_values["amount"],
                    )
                    != 0
                    or existing.date != line_values["date"]
                ):
                    raise UserError(
                        _(
                            "A bank transaction identity already exists with different accounting facts.",
                        ),
                    )
                prior_provider_id = existing_provider_ids.get(existing.id)
                if prior_provider_id and prior_provider_id != raw_id:
                    raise UserError(
                        _(
                            "Two source transactions resolve to the same migrated bank line.",
                        ),
                    )
                existing_provider_ids[existing.id] = raw_id
                existing_lines |= existing
                continue
            line_values.update(
                {
                    "provider_code": config.provider,
                    "provider_account_id": canonical_account_id,
                    "provider_transaction_id": raw_id,
                    "provider_identity_kind": "stable",
                    "transaction_details": {
                        "provider": config.provider,
                        "account_id": canonical_account_id,
                        "transaction_id": raw_id,
                    },
                    "ingestion_file_ids": [Command.link(self.id)],
                },
            )
            line_values["sequence"] = ordinal
            new_values.append(line_values)
        statement = self._get_or_create_statement(
            config,
            period_start,
            period_end,
            values,
            existing_lines,
            new_values,
        )
        if statement:
            for line in existing_lines:
                update = {"ingestion_file_ids": [Command.link(self.id)]}
                if not line.provider_transaction_id:
                    raw_id = existing_provider_ids.get(line.id)
                    if raw_id:
                        update.update(
                            {
                                "provider_code": config.provider,
                                "provider_account_id": canonical_account_id,
                                "provider_transaction_id": raw_id,
                                "provider_identity_kind": "stable",
                            },
                        )
                line.with_context(bank_review_internal=True).write(update)
            self.write(
                {
                    "statement_id": statement.id,
                    "period_start": period_start,
                    "period_end": period_end,
                    "processing_state": "duplicate" if not new_values else "processed",
                    "processing_detail": (
                        _(
                            "All %(count)s bank transactions were already present and linked.",
                            count=len(existing_lines),
                        )
                        if not new_values
                        else _(
                            "Imported %(new)s new transaction(s); %(existing)s already present.",
                            new=len(new_values),
                            existing=len(existing_lines),
                        )
                    ),
                },
            )
            self._associate_period_pdfs(statement)
            self.ingestion_id.exception_ids.filtered(
                lambda item: not item.statement_id,
            ).sudo().with_context(bank_exception_internal=True).write(
                {"statement_id": statement.id},
            )
        for ordinal, raw_id, candidate in ambiguous:
            exception = self._ensure_exception(
                "identity",
                _(
                    "Transaction %(ordinal)s needs an identity decision",
                    ordinal=ordinal,
                ),
                (
                    _("The OFX transaction identifier is duplicated in this file.")
                    if raw_id in duplicate_ids
                    else _("The OFX transaction has no stable bank identifier.")
                ),
                statement=statement,
            )
            exception.sudo().with_context(bank_exception_internal=True).write(
                {"candidate_values": candidate},
            )
        if ambiguous:
            self.processing_state = "attention"

    @api.model
    def _with_parser_fitid_placeholders(self, content):
        """Let the maintained OFX parser expose rows lacking a stable FITID.

        The source bytes remain untouched. Placeholders exist only in the
        parser copy and are replaced by a file-scoped candidate identity before
        any line can be approved.
        """
        ordinal = 0

        def add_fitid(match):
            nonlocal ordinal
            block = match.group(0)
            fitid = re.search(rb"<FITID(?:\s[^>]*)?>\s*([^<]*)", block, re.I)
            if fitid and fitid.group(1).strip():
                return block
            ordinal += 1
            block = re.sub(
                rb"<FITID(?:\s[^>]*)?>\s*</FITID\s*>|<FITID\s*/>",
                b"",
                block,
                flags=re.I,
            )
            marker = f"<FITID>{MISSING_FITID_PREFIX}{ordinal:06d}</FITID>".encode()
            return re.sub(
                rb"</STMTTRN\s*>",
                marker + b"</STMTTRN>",
                block,
                count=1,
                flags=re.I,
            )

        return re.sub(
            rb"<STMTTRN(?:\s[^>]*)?>.*?</STMTTRN\s*>",
            add_fitid,
            content,
            flags=re.I | re.S,
        )

    def _find_existing_transaction(
        self,
        config,
        raw_id,
        unique_import_id,
        transaction_date,
        amount,
    ):
        Line = self.env["account.bank.statement.line"].sudo()
        existing = Line.search(
            [
                ("journal_id", "=", config.journal_id.id),
                ("provider_code", "=", config.provider),
                (
                    "provider_account_id",
                    "=",
                    sanitize_account_number(config.source_account_identifier),
                ),
                ("provider_transaction_id", "=", raw_id),
            ],
            limit=1,
        )
        if not existing:
            existing = Line.search(
                [
                    ("journal_id", "=", config.journal_id.id),
                    ("unique_import_id", "=", unique_import_id),
                ],
                limit=1,
            )
        if not existing:
            candidates = Line.search(
                [
                    ("journal_id", "=", config.journal_id.id),
                    ("date", "=", transaction_date),
                ],
            )
            exact = candidates.filtered(
                lambda line: self._historical_extra_id(line) == raw_id,
            )
            if len(exact) > 1:
                matching_facts = exact.filtered(
                    lambda line: line.currency_id.compare_amounts(
                        line.amount,
                        amount,
                    )
                    == 0,
                )
                if len(matching_facts) != 1:
                    raise UserError(
                        _(
                            "The migrated bank history contains a conflicting transaction identity.",
                        ),
                    )
                exact = matching_facts
            existing = exact[:1]
            if not existing:
                matching_facts = candidates.filtered(
                    lambda line: line.currency_id.compare_amounts(
                        line.amount,
                        amount,
                    )
                    == 0,
                )
                if len(matching_facts) == 1:
                    candidate = matching_facts
                    historical_id = self._historical_extra_id(candidate)
                    shared_historical_id = candidates.filtered(
                        lambda line: (
                            line != candidate
                            and historical_id
                            and self._historical_extra_id(line) == historical_id
                        ),
                    )
                    if shared_historical_id:
                        existing = candidate
            if existing and not existing.unique_import_id:
                existing.with_context(
                    bank_review_internal=True,
                ).unique_import_id = unique_import_id
        return existing

    @api.model
    def _historical_extra_id(self, line):
        details = line.transaction_details or {}
        return str((details.get("extra") or {}).get("id") or "").strip()

    def _get_or_create_statement(
        self,
        config,
        period_start,
        period_end,
        values,
        existing_lines,
        new_values,
    ):
        if not period_start or not period_end:
            raise UserError(
                _("The transaction candidate has no unambiguous statement period."),
            )
        self.env.cr.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))",
            [f"account.bank.period:{config.id}:{period_start.isoformat()}"],
        )
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
                raise UserError(
                    _(
                        "Existing transactions are split across multiple bank statements.",
                    ),
                )
            candidate = existing_statements
            candidate_dates = candidate.line_ids.filtered(
                lambda line: line.state == "posted",
            ).mapped("date")
            if (
                candidate.ingestion_config_id
                or not candidate_dates
                or min(candidate_dates).replace(day=1) != period_start
                or _month_end(max(candidate_dates)) != period_end
            ):
                raise UserError(
                    _(
                        "The existing statement cannot be adopted without changing historical membership.",
                    ),
                )
            candidate.with_context(bank_review_internal=True).write(
                {
                    "ingestion_config_id": config.id,
                    "period_start": period_start,
                    "period_end": period_end,
                },
            )
            statement = candidate
        if not statement and not new_values and not existing_lines:
            return Statement
        create_commands = [Command.create(value) for value in new_values]
        if not statement:
            statement_values = {
                "name": _(
                    "%(journal)s — %(month)s",
                    journal=config.journal_id.code,
                    month=format_date(self.env, period_start, date_format="MMMM y"),
                ),
                "reference": self.filename,
                "ingestion_config_id": config.id,
                "period_start": period_start,
                "period_end": period_end,
                "balance_start": values.get("balance_start", 0),
                "balance_end_real": values.get("balance_end_real", 0),
                "line_ids": [Command.set(existing_lines.ids), *create_commands],
            }
            statement = Statement.with_context(bank_review_internal=True).create(
                statement_values,
            )
        elif create_commands:
            statement.with_context(bank_review_internal=True).write(
                {"line_ids": create_commands},
            )
        outside = existing_lines.filtered(
            lambda line: line.statement_id and line.statement_id != statement,
        )
        if outside:
            raise UserError(
                _(
                    "An exact transaction identity already belongs to another statement.",
                ),
            )
        unassigned = existing_lines.filtered(lambda line: not line.statement_id)
        if unassigned:
            unassigned.with_context(bank_review_internal=True).write(
                {"statement_id": statement.id},
            )
        if not statement.balances_confirmed:
            statement.with_context(bank_review_internal=True).write(
                {
                    "balance_start": values.get(
                        "balance_start",
                        statement.balance_start,
                    ),
                    "balance_end_real": values.get(
                        "balance_end_real",
                        statement.balance_end_real,
                    ),
                },
            )
        self.ingestion_id.exception_ids.filtered(
            lambda item: not item.statement_id,
        ).sudo().with_context(bank_exception_internal=True).write(
            {"statement_id": statement.id},
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
                        "processing_detail": _(
                            "This exact PDF is already the accepted evidence.",
                        ),
                    },
                )
            elif not candidate.evidence_status:
                candidate.evidence_status = "candidate"
        if not statement.accepted_evidence_id and candidates:
            candidates[0]._accept_evidence()

    def _associate_pdf(self):
        self.ensure_one()
        integrity_error = self._pdf_integrity_error(self._content())
        if integrity_error:
            self.write(
                {
                    "processing_state": "failed",
                    "processing_detail": integrity_error,
                    "evidence_status": "candidate",
                },
            )
            self._ensure_exception(
                "evidence",
                _("Replace the damaged bank statement"),
                integrity_error,
            )
            return
        period_start = self.ingestion_id.period_start
        period_end = self.ingestion_id.period_end
        if not period_start or not period_end:
            self.write(
                {
                    "processing_state": "attention",
                    "processing_detail": _(
                        "The statement period could not be determined from the email subject.",
                    ),
                    "evidence_status": "candidate",
                },
            )
            self._ensure_exception(
                "evidence",
                _("Confirm the PDF statement period"),
                _("Set an unambiguous period on the received export, then retry."),
            )
            return
        values = {
            "period_start": period_start,
            "period_end": period_end,
            "processing_state": "processed",
            "processing_detail": _("Official PDF retained unchanged."),
        }
        # Retrying a fully retained source must not demote the statement's
        # accepted evidence. The Documents archive worker deliberately selects
        # only accepted files, so changing the same record back to candidate
        # (and then duplicate below) would strand it outside the queue forever.
        if self.evidence_status != "accepted":
            values["evidence_status"] = "candidate"
        self.write(values)
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
            if statement.accepted_evidence_id == self:
                self.sudo().write(
                    {
                        "evidence_status": "accepted",
                        "processing_state": "processed",
                        "processing_detail": _(
                            "This PDF remains the accepted official evidence.",
                        ),
                    },
                )
            elif not statement.accepted_evidence_id:
                self._accept_evidence()
            elif statement.accepted_evidence_id.sha256 == self.sha256:
                self.write(
                    {
                        "evidence_status": "duplicate",
                        "processing_state": "duplicate",
                        "processing_detail": _(
                            "This exact PDF is already the accepted evidence.",
                        ),
                    },
                )
            else:
                self._ensure_exception(
                    "evidence",
                    _("Replacement bank statement received"),
                    _(
                        "A new PDF was retained. Review it before replacing the accepted evidence.",
                    ),
                    statement=statement,
                )
        else:
            imported_statements = self.ingestion_id.file_ids.statement_id
            if len(imported_statements) == 1:
                self._ensure_exception(
                    "evidence",
                    _("Bank statement PDF period does not match"),
                    _(
                        "The PDF period inferred from the email does not match the OFX month. Review the source before accepting evidence.",
                    ),
                    statement=imported_statements,
                )

    def action_accept_evidence(self):
        if not is_accounting_operator(self.env.user):
            raise AccessError(
                _("Only an accountant can accept bank statement evidence."),
            )
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
        if (
            statement.certification_state == "certified"
            and statement.accepted_evidence_id != self
        ):
            raise UserError(
                _(
                    "Reopen the certified statement before accepting replacement evidence.",
                ),
            )
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
            },
        )
        statement.message_post(
            body=_(
                "Official bank statement evidence accepted: %(name)s",
                name=self.filename,
            ),
            attachment_ids=[self.attachment_id.id],
        )
        self.exception_ids.filtered(
            lambda item: item.kind == "evidence" and item.state == "open",
        ).sudo().with_context(bank_exception_internal=True).write(
            {
                "state": "resolved",
                "resolution": "accept_evidence",
                "resolution_reason": _(
                    "Accepted as the official evidence for this review.",
                ),
                "resolved_by_id": self.env.user.id,
                "resolved_at": fields.Datetime.now(),
            },
        )
        self.ingestion_id._refresh_processing_state()
        self._bank_evidence_accepted()

    def _bank_evidence_accepted(self):
        """Optional bridge hook for durable document archives."""
        return

    def _ensure_exception(self, kind, name, detail, statement=False):
        return self.ingestion_id._ensure_exception(
            kind,
            name,
            detail,
            file=self,
            statement=statement or self.statement_id,
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
            "recovered_upload",
        }
        if immutable.intersection(vals) and self.filtered(lambda record: record.id):
            raise AccessError(_("Retained source file identity is immutable."))
        operational = {
            "processing_state",
            "processing_detail",
            "statement_id",
            "period_start",
            "period_end",
            "parsed_balance_start",
            "parsed_balance_end_real",
            "evidence_status",
            "paperless_version",
        }
        if operational.intersection(vals) and not self.env.su:
            raise AccessError(
                _(
                    "Use the bank export review actions to change source processing or evidence state.",
                ),
            )
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
