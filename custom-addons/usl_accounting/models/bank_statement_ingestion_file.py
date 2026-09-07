"""Retained bank export files: identity, parsing, statement creation and evidence archiving."""

import codecs
import hashlib
import mimetypes
import re
import zipfile
from io import BytesIO
from pathlib import PurePosixPath

from lxml import etree

from odoo import (
    Command,
    _,
    api,
    fields,
    models,
)
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import BinaryBytes, format_date
from odoo.tools.pdf import PdfReader

from .bank_statement_ingestion import (
    MAX_ARCHIVE_BYTES,
    MAX_ARCHIVE_MEMBERS,
    MAX_COMPRESSION_RATIO,
    MISSING_FITID_PREFIX,
    OFX_XML_COMPATIBILITY_HEADER,
    _month_end,
    _ofx_account_matches,
)
from .bank_statement_review import is_accounting_operator
from odoo.addons.base.models.res_partner_bank import sanitize_account_number


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
            ("ignored", "Intentionally ignored"),
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

    def _finalize_supplemental_file(self):
        self.ensure_one()
        prior = self.search(
            [
                ("ingestion_id.config_id", "=", self.ingestion_id.config_id.id),
                ("id", "<", self.id),
                ("sha256", "=", self.sha256),
                ("classification", "=", self.classification),
                ("processing_state", "in", ("processed", "duplicate", "ignored")),
            ],
            order="id",
            limit=1,
        )
        if prior:
            values = {
                "processing_state": "duplicate",
                "processing_detail": _(
                    "An identical retained file already has a final disposition on this bank import route.",
                ),
            }
        else:
            explanations = {
                "csv": _(
                    "CSV copy retained unchanged. OFX is the authoritative transaction import for this route.",
                ),
                "qif": _(
                    "QIF copy retained unchanged. OFX is the authoritative transaction import for this route.",
                ),
                "unsupported": _(
                    "File retained unchanged and intentionally ignored because this file type is not used by automated bank import.",
                ),
            }
            values = {
                "processing_state": "ignored",
                "processing_detail": explanations[self.classification],
            }
        self.sudo().write(values)
        self.exception_ids.filtered(
            lambda item: item.kind == "unsupported" and item.state == "open",
        ).sudo().with_context(bank_exception_internal=True).write(
            {
                "state": "resolved",
                "resolution": "not_relevant",
                "resolution_reason": _(
                    "The retained supplemental file is not an input to automated bank import.",
                ),
                "resolved_by_id": self.env.user.id,
                "resolved_at": fields.Datetime.now(),
            },
        )

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
        parser_content = self._normalize_ofx_parser_content(content)
        parser_content = self._with_parser_fitid_placeholders(parser_content)
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
            detail = _(
                "The OFX account does not match the account configured for this route.",
            )
            self._ensure_exception(
                "account",
                _("Bank account does not match"),
                detail,
            )
            self.sudo().write(
                {
                    "processing_state": "failed",
                    "processing_detail": detail,
                },
            )
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
        raw_statement = ofx.accounts[0].statement
        header_start = fields.Date.to_date(getattr(raw_statement, "start_date", None))
        header_end = fields.Date.to_date(getattr(raw_statement, "end_date", None))
        if header_start or header_end:
            if (not header_start or not header_end
                    or header_start != period_start or header_end != period_end):
                raise UserError(_("The OFX coverage must match its single calendar month."))
            period_start, period_end = header_start, header_end
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
            self.exception_ids.filtered(
                lambda item: item.kind == "import" and item.state == "open",
            ).sudo().with_context(bank_exception_internal=True).write(
                {
                    "state": "resolved",
                    "resolution": "corrected_source",
                    "resolution_reason": _(
                        "The retained OFX source parsed and imported successfully on retry.",
                    ),
                    "resolved_by_id": self.env.user.id,
                    "resolved_at": fields.Datetime.now(),
                },
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
            self.sudo().write(
                {
                    "processing_state": "failed",
                    "processing_detail": _(
                        "One or more transactions need an identity decision before this OFX file can be completed.",
                    ),
                },
            )

    @api.model
    def _normalize_ofx_parser_content(self, content):
        """Return a parser-only UTF-8 copy for OFX 2.x XML.

        ofxparse reads legacy OFX headers before it examines XML processing
        instructions. Without an ``ENCODING:`` header it decodes the stream as
        ASCII. Keep legacy SGML byte-for-byte compatible and add the header only
        to a validated, in-memory OFX XML copy.
        """
        stripped = content.lstrip()
        if stripped.upper().startswith(b"OFXHEADER:"):
            return content

        probe = content[:8192]
        if probe.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
            probe_text = probe.decode("utf-32", errors="ignore")
        elif probe.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
            probe_text = probe.decode("utf-16", errors="ignore")
        else:
            probe_text = probe.decode("latin-1")

        xml_declaration = re.search(
            r"<\?xml\b[^>]*\bencoding\s*=\s*(['\"])([^'\"]+)\1",
            probe_text,
            re.I,
        )
        ofx_instruction = re.search(
            r"<\?ofx\b([^>]*)\?>",
            probe_text,
            re.I,
        )
        ofx_encoding = (
            re.search(
                r"\bencoding\s*=\s*(['\"])([^'\"]+)\1",
                ofx_instruction.group(1),
                re.I,
            )
            if ofx_instruction
            else None
        )
        xml_encoding = xml_declaration.group(2).strip() if xml_declaration else None
        instruction_encoding = (
            ofx_encoding.group(2).strip() if ofx_encoding else None
        )
        explicit_xml = bool(xml_declaration or ofx_instruction)
        unsupported_encoding_message = _(
            "The OFX XML declares an unsupported encoding: %(encoding)s.",
        )

        def resolve_encoding(name):
            try:
                return codecs.lookup(name).name
            except (LookupError, TypeError) as error:
                raise UserError(
                    unsupported_encoding_message
                    % {"encoding": name or _("empty")},
                ) from error

        resolved_xml = resolve_encoding(xml_encoding) if xml_encoding else None
        resolved_instruction = (
            resolve_encoding(instruction_encoding)
            if instruction_encoding
            else None
        )
        if (
            resolved_xml
            and resolved_instruction
            and resolved_xml != resolved_instruction
        ):
            raise UserError(
                _(
                    "The OFX XML declares conflicting encodings: %(xml)s and %(ofx)s.",
                    xml=xml_encoding,
                    ofx=instruction_encoding,
                ),
            )
        encoding = resolved_xml or resolved_instruction or "utf-8"
        try:
            xml_text = content.decode(encoding).lstrip("\ufeff")
        except UnicodeError as error:
            raise UserError(
                _(
                    "The OFX XML is not valid %(encoding)s text.",
                    encoding=xml_encoding or instruction_encoding or "UTF-8",
                ),
            ) from error

        parser_xml = re.sub(
            r"(<\?xml\b[^>]*\bencoding\s*=\s*['\"])[^'\"]+(['\"])",
            r"\1UTF-8\2",
            xml_text,
            count=1,
            flags=re.I,
        ).encode("utf-8")
        try:
            root = etree.fromstring(
                parser_xml,
                parser=etree.XMLParser(
                    recover=False,
                    resolve_entities=False,
                    no_network=True,
                ),
            )
        except (etree.XMLSyntaxError, ValueError) as error:
            if not explicit_xml:
                return content
            detail = getattr(error, "msg", None) or str(error).splitlines()[0]
            raise UserError(
                _("The OFX XML attachment is malformed: %(detail)s", detail=detail),
            ) from error
        if etree.QName(root).localname.upper() != "OFX":
            if not explicit_xml:
                return content
            raise UserError(_("The XML attachment does not contain an OFX document."))
        return OFX_XML_COMPATIBILITY_HEADER + parser_xml

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
        periods = self.ingestion_id._verified_file_periods(pdf=self)
        if len(periods) > 1:
            raise UserError(_("The retained bank files disagree on the statement period."))
        if periods:
            period_start, period_end = periods.pop()
            self.ingestion_id.write({"period_start": period_start, "period_end": period_end})
        else:
            period_start = self.ingestion_id.period_start
            period_end = self.ingestion_id.period_end
            if not period_start or not period_end:
                period_start, period_end = self.ingestion_id._period_from_text(self.ingestion_id.subject)
        if not period_start or not period_end:
            self.write(
                {
                    "processing_state": "failed",
                    "processing_detail": _(
                        "The retained files and email do not establish a statement period.",
                    ),
                    "evidence_status": "candidate",
                },
            )
            self._ensure_exception(
                "evidence",
                _("Confirm the PDF statement period"),
                _("Use Correct period on the received export after checking the bank statement."),
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
        period_exception_names = {
            "Confirm the PDF statement period", _("Confirm the PDF statement period"),
        }
        self.exception_ids.filtered(
            lambda item: item.state == "open" and item.kind == "evidence"
            and item.name in period_exception_names,
        ).with_context(bank_exception_internal=True).write({
            "state": "resolved", "resolution": "corrected_source",
            "resolution_reason": _("The retained PDF now has an established statement period."),
            "resolved_by_id": self.env.user.id,
            "resolved_at": fields.Datetime.now(),
        })
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
                        "The PDF period does not match the OFX month. Review the source before accepting evidence.",
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
