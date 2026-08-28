import base64
import re
import time
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal

ACCOUNTING_CUTOFF = date(2025, 10, 1)
JOURNAL_PROVIDERS = {
    "ETSY": "etsy",
    "MEDU": "medusa",
    "PFW": "printful",
    "STRP": "stripe",
    "RVMT": "revolut",
}
EXPECTED_JOURNALS = {
    "ETSY": (24, Decimal("8055.09")),
    "MEDU": (28, Decimal("12241.55")),
    "PFW": (37, Decimal("7958.08")),
    "STRP": (33, Decimal("4832.79")),
    "RVMT": (58, Decimal("5182.21")),
}
EXPECTED_DIRECT_RELATIONSHIPS = 14
EXPECTED_DIRECT_EVENTS = 10
EXPECTED_BANK_RELATIONSHIPS = 81
BANK_REFERENCE = re.compile(
    r"((?:BNK1|BNK2|REVUS|REVGB)/\d{2}-\d{2}/\d{4})",
)


def accounting_link_type(reference):
    reference = (reference or "").lower()
    if any(token in reference for token in ("refund", "complaint", "dispute")):
        return "refund"
    if ":fees" in reference:
        return "fee"
    if any(token in reference for token in ("payout", "topup", "growth")):
        return "payout"
    if any(token in reference for token in ("consumption", "fulfillment")):
        return "cogs"
    if any(token in reference for token in (":sales:", ":sale:", ":wallet:")):
        return "revenue"
    return "clearing"


class B2cRelationshipFinalizer:
    """Migration-only matching of locked source evidence to durable product records."""

    def __init__(self, run, source, company, attachments):
        self.run = run
        self.env = run.env
        self.source = source
        self.company = company
        self.attachments = attachments
        self._sessions = {}

    def _session(self, provider, value_date, *, required=False):
        provider = "medusa" if provider == "medusa_legacy" else provider
        period_start = value_date.replace(day=1)
        key = (provider, period_start)
        if key not in self._sessions:
            sessions = (
                self.env["b2c.accounting.session"]
                .sudo()
                .search(
                    [
                        ("company_id", "=", self.company.id),
                        ("period_start", "=", period_start),
                        ("channel_id", "=", False),
                        ("source_provider", "=", provider),
                    ],
                    limit=2,
                )
            )
            if len(sessions) > 1:
                raise RuntimeError(
                    f"Ambiguous B2C accounting session for {provider} {period_start}",
                )
            self._sessions[key] = sessions
        session = self._sessions[key]
        if required and not session:
            session = self.env["b2c.accounting.session"].sudo().create(
                {
                    "company_id": self.company.id,
                    "period_start": period_start,
                    "channel_id": False,
                    "source_provider": provider,
                    "review_note": (
                        "The verified provider journal covers this accounting month, "
                        "while the locked event export contains no individual record "
                        "for it. No order or event allocation is claimed."
                    ),
                },
            )
            self._sessions[key] = session
        return session

    def _upsert_link(self, values):
        subject_fields = (
            "order_id",
            "payment_event_id",
            "fulfilment_event_id",
            "session_id",
        )
        target_fields = (
            "account_move_id",
            "account_move_line_id",
            "bank_statement_line_id",
            "account_payment_id",
            "payment_transaction_id",
            "sale_order_id",
            "stock_picking_id",
            "stock_move_id",
            "attachment_id",
        )
        domain = [
            ("company_id", "=", self.company.id),
            ("link_type", "=", values["link_type"]),
        ]
        for field_name in (*subject_fields, *target_fields):
            domain.append((field_name, "=", values.get(field_name) or False))
        links = self.env["b2c.accounting.link"].sudo().search(domain, limit=2)
        if len(links) > 1:
            raise RuntimeError(f"Ambiguous existing B2C relationship: {domain}")
        if links:
            links.write(values)
            return links
        return self.env["b2c.accounting.link"].sudo().create(values)

    def _critical_moves(self):
        moves = (
            self.env["account.move"]
            .sudo()
            .search(
                [
                    ("company_id", "=", self.company.id),
                    ("state", "=", "posted"),
                    ("date", ">=", ACCOUNTING_CUTOFF),
                    ("journal_id.code", "in", list(JOURNAL_PROVIDERS)),
                ],
                order="date, id",
            )
        )
        by_code = defaultdict(lambda: self.env["account.move"])
        for move in moves:
            by_code[move.journal_id.code] |= move
        actual = {}
        for code, expected in EXPECTED_JOURNALS.items():
            journal_moves = by_code[code]
            debit = sum(
                (Decimal(str(value)) for value in journal_moves.line_ids.mapped("debit")),
                Decimal(),
            )
            credit = sum(
                (Decimal(str(value)) for value in journal_moves.line_ids.mapped("credit")),
                Decimal(),
            )
            actual[code] = (len(journal_moves), debit, credit)
            if (len(journal_moves), debit) != expected or credit != expected[1]:
                raise RuntimeError(
                    f"Critical {code} ledger fingerprint changed: "
                    f"{actual[code]} != {expected}",
                )
        if len(moves) != 180:
            raise RuntimeError(f"Expected 180 critical B2C moves, found {len(moves)}")
        return moves, actual

    def _link_sessions_and_banks(self, moves):
        bank_count = 0
        for move in moves:
            provider = JOURNAL_PROVIDERS[move.journal_id.code]
            session = self._session(provider, move.date, required=True)
            self._upsert_link(
                {
                    "name": f"{move.journal_id.code} monthly ledger: {move.name}",
                    "company_id": self.company.id,
                    "session_id": session.id,
                    "account_move_id": move.id,
                    "link_type": accounting_link_type(move.ref),
                    "link_state": "verified",
                    "evidence_note": (
                        "Exact journal, provider, company and accounting-month match. "
                        "This is aggregate evidence, not an allocation to an individual "
                        "order."
                    ),
                },
            )
            references = BANK_REFERENCE.findall(move.ref or "")
            if len(references) > 1:
                raise RuntimeError(f"Move {move.name} names multiple bank transactions")
            if not references:
                continue
            bank_moves = self.env["account.move"].sudo().search(
                [
                    ("company_id", "=", self.company.id),
                    ("name", "=", references[0]),
                ],
                limit=2,
            )
            if len(bank_moves) != 1:
                raise RuntimeError(
                    f"Bank reference {references[0]} from {move.name} is not unique",
                )
            statement_lines = self.env["account.bank.statement.line"].sudo().search(
                [("move_id", "=", bank_moves.id)],
                limit=2,
            )
            if len(statement_lines) != 1:
                raise RuntimeError(
                    f"Bank reference {references[0]} has no unique statement line",
                )
            self._upsert_link(
                {
                    "name": f"Native bank transaction: {references[0]}",
                    "company_id": self.company.id,
                    "session_id": session.id,
                    "account_move_id": move.id,
                    "bank_statement_line_id": statement_lines.id,
                    "link_type": "bank",
                    "link_state": "verified",
                    "evidence_note": (
                        "The journal reference names one unique native bank statement "
                        "line."
                    ),
                },
            )
            bank_count += 1
        if bank_count != EXPECTED_BANK_RELATIONSHIPS:
            raise RuntimeError(
                f"Expected {EXPECTED_BANK_RELATIONSHIPS} bank relationships, "
                f"found {bank_count}",
            )
        return bank_count

    @staticmethod
    def _event_identifier_candidates(event):
        return tuple(
            (field_name, event[field_name])
            for field_name in (
                "external_transaction_id",
                "external_original_payment_id",
                "external_payout_id",
                "external_refund_id",
                "external_payment_intent_id",
            )
            if event[field_name] and len(event[field_name]) >= 8
        )

    def _link_direct_events(self, moves):
        moves_by_provider = defaultdict(lambda: self.env["account.move"])
        for move in moves:
            moves_by_provider[JOURNAL_PROVIDERS[move.journal_id.code]] |= move
        events = self.env["b2c.payment.event"].sudo().search(
            [
                ("company_id", "=", self.company.id),
                ("source_provider", "in", ("stripe", "revolut")),
            ],
        )
        identifier_occurrences = Counter()
        for event in events:
            for field_name, identifier in self._event_identifier_candidates(event):
                identifier_occurrences[event.source_provider, field_name, identifier] += 1

        accepted = []
        for event in events:
            for field_name, identifier in self._event_identifier_candidates(event):
                matches = moves_by_provider[event.source_provider].filtered(
                    lambda move, value=identifier: value in (move.ref or ""),
                )
                if not matches:
                    continue
                if len(matches) != 1:
                    raise RuntimeError(
                        f"Direct identifier {identifier} matches {len(matches)} moves",
                    )
                if identifier_occurrences[event.source_provider, field_name, identifier] != 1:
                    raise RuntimeError(
                        f"Direct identifier {identifier} is duplicated on B2C events",
                    )
                move = matches
                comparison_event = event
                if field_name == "external_original_payment_id":
                    comparison_event = event.original_event_id
                    if (
                        not comparison_event
                        or comparison_event.external_transaction_id != identifier
                    ):
                        raise RuntimeError(
                            f"Original-event semantics failed for {identifier}",
                        )
                if move.date != comparison_event.event_date.date():
                    raise RuntimeError(
                        f"Direct identifier {identifier} has a date mismatch",
                    )
                amount_lines = move.line_ids.filtered(
                    lambda line: line.currency_id == comparison_event.currency_id
                    and Decimal(str(line.amount_currency))
                    == Decimal(str(comparison_event.amount)),
                )
                if len(amount_lines) != 1:
                    raise RuntimeError(
                        f"Direct identifier {identifier} failed currency/sign/amount semantics",
                    )
                self._upsert_link(
                    {
                        "name": f"Verified provider event: {identifier}",
                        "company_id": self.company.id,
                        "payment_event_id": event.id,
                        "account_move_id": move.id,
                        "account_move_line_id": amount_lines.id,
                        "link_type": (
                            "clearing"
                            if field_name == "external_original_payment_id"
                            else accounting_link_type(move.ref)
                        ),
                        "link_state": "verified",
                        "evidence_note": (
                            f"Unique immutable {field_name}, provider, date, currency, "
                            "sign and amount match."
                        ),
                    },
                )
                accepted.append((event.id, move.id, field_name))
        if len(accepted) != EXPECTED_DIRECT_RELATIONSHIPS:
            raise RuntimeError(
                f"Expected {EXPECTED_DIRECT_RELATIONSHIPS} direct relationships, "
                f"found {len(accepted)}",
            )
        if len({event_id for event_id, _move_id, _field in accepted}) != EXPECTED_DIRECT_EVENTS:
            raise RuntimeError(
                f"Expected {EXPECTED_DIRECT_EVENTS} directly linked events",
            )
        return accepted

    def _session_has_move_evidence(self, session):
        return bool(
            session.accounting_link_ids.filtered(
                lambda link: link.link_state == "verified" and link.account_move_id,
            ),
        )

    def _session_has_bank_evidence(self, session):
        return bool(
            session.accounting_link_ids.filtered(
                lambda link: link.link_state == "verified"
                and link.bank_statement_line_id,
            ),
        )

    def _set_record_dispositions(self):
        disposition_counts = Counter()
        models_and_dates = (
            ("b2c.order", "order_date"),
            ("b2c.payment.event", "event_date"),
            ("b2c.fulfilment.event", "event_date"),
        )
        for model_name, date_field in models_and_dates:
            records = self.env[model_name].sudo().search(
                [("company_id", "=", self.company.id)],
            )
            for record in records:
                record_date = record[date_field].date()
                direct = bool(
                    record.accounting_link_ids.filtered(
                        lambda link: link.link_state == "verified"
                        and bool(link.account_move_line_id),
                    ),
                )
                session = self._session(record.source_provider, record_date)
                if record_date < ACCOUNTING_CUTOFF:
                    state = "not_applicable"
                    note = (
                        "Before the 1 October 2025 accounting reconstruction cutoff; "
                        "no accounting allocation is claimed."
                    )
                elif direct:
                    state = "verified"
                    note = (
                        "A unique provider identifier and matching accounting line "
                        "verify this direct relationship."
                    )
                elif session and self._session_has_move_evidence(session):
                    state = "partial"
                    note = (
                        f"Covered by verified aggregate session {session.display_name}; "
                        "no individual accounting allocation is invented."
                    )
                else:
                    state = "not_applicable"
                    note = (
                        "The locked source snapshot contains no corresponding provider "
                        "ledger for this accounting month; no allocation is invented."
                    )
                bank_state = (
                    "partial"
                    if session and self._session_has_bank_evidence(session)
                    else "not_applicable"
                )
                values = {
                    "accounting_link_state": state,
                    "accounting_link_note": note,
                }
                if "bank_link_state" in record._fields:
                    values["bank_link_state"] = bank_state
                if "conversion_state" in record._fields and record.conversion_state == "pending":
                    values.update(
                        {
                            "conversion_state": "not_applicable",
                            "conversion_evidence": (
                                "No defensible per-event company-currency allocation exists; "
                                "company-currency truth is retained only at verified aggregate "
                                "session level where available."
                            ),
                        },
                    )
                record.write(values)
                disposition_counts[state] += 1

        orders = self.env["b2c.order"].sudo().search(
            [("company_id", "=", self.company.id)],
        )
        for order in orders:
            line_states = set(order.line_ids.mapped("mapping_state"))
            mapping_state = (
                "not_applicable"
                if not line_states or line_states == {"not_applicable"}
                else "verified"
                if line_states == {"verified"}
                else "partial"
            )
            order.write(
                {
                    "mapping_state": mapping_state,
                    "payment_link_state": (
                        "verified" if order.payment_event_ids else "not_applicable"
                    ),
                    "fulfilment_link_state": (
                        "verified" if order.fulfilment_event_ids else "not_applicable"
                    ),
                },
            )
        for model_name in ("b2c.payment.event", "b2c.fulfilment.event"):
            records = self.env[model_name].sudo().search(
                [("company_id", "=", self.company.id)],
            )
            records.filtered("order_id").write({"order_link_state": "verified"})
            (records - records.filtered("order_id")).write(
                {"order_link_state": "not_applicable"},
            )
        return dict(disposition_counts)

    def _document_by_checksum(self, descriptor):
        checksum = descriptor["sha256"]
        documents = self.env["usl.document"].sudo().search(
            [
                ("availability_state", "=", "available"),
                "|",
                ("checksum", "=", checksum),
                ("version_ids.checksum", "=", checksum),
            ],
        )
        if len(documents) > 1:
            raise RuntimeError(
                f"B2C source document checksum is duplicated: {descriptor['source'].name}",
            )
        return documents

    def _upload_document(self, descriptor):
        source_file = descriptor["source"]
        attachment = self.attachments.get(source_file.name)
        res_model = False
        res_id = False
        allowed = self.env["usl.document.link"]._allowed_models()
        if attachment and attachment.res_model in allowed and attachment.res_id:
            if self.env[attachment.res_model].browse(attachment.res_id).exists():
                res_model = attachment.res_model
                res_id = attachment.res_id
        result = (
            self.env["usl.document"]
            .sudo()
            .with_context(allowed_company_ids=[self.company.id])
            .with_company(self.company)
            .upload_from_odoo(
                source_file.name,
                base64.b64encode(descriptor["content"]).decode(),
                source_file.mimetype,
                res_model=res_model,
                res_id=res_id,
                company_id=self.company.id,
                confidentiality="internal",
                source="odoo_upload",
            )
        )
        if result.get("document_id"):
            return self.env["usl.document"].sudo().browse(result["document_id"])
        operation = self.env["usl.document.operation"].sudo().browse(
            result.get("operation_id"),
        )
        if not operation:
            raise RuntimeError(f"Archive ingestion failed for {source_file.name}: {result}")
        for _attempt in range(30):
            operation.poll()
            operation.invalidate_recordset()
            if operation.state == "archived" and operation.document_id:
                return operation.document_id
            if operation.state in {"failed", "duplicate"}:
                break
            time.sleep(0.5)
        raise RuntimeError(
            f"Archive ingestion did not complete for {source_file.name}: "
            f"{operation.state} {operation.error_message or ''}",
        )

    def _link_document(self, document, record):
        if not record:
            return self.env["usl.document.link"]
        link = (
            self.env["usl.document.link"]
            .sudo()
            .with_context(
                allowed_company_ids=[self.company.id],
                usl_documents_defer_access_sync=True,
            )
            .create_for_record(
                document,
                record._name,
                record.id,
                archive_mode="mandatory",
                policy_role="evidence",
                attachment_origin="migration",
                policy_reason="b2c_source_package_exact_evidence",
            )
        )
        # Earlier rebuilds created these exact-checksum relationships through
        # the generic Documents helper. Repair their policy in place so a
        # repeated migration converges instead of retaining a false manual
        # review task.
        link.with_context(usl_documents_link_policy_write=True).write(
            {
                "archive_mode": "mandatory",
                "policy_role": "evidence",
                "document_role": "evidence",
                "attachment_origin": "migration",
                "policy_reason": "b2c_source_package_exact_evidence",
            },
        )
        return link

    def _finalize_documents(self):
        if len(self.source["files"]) != 40:
            message = "The canonical B2C source package must contain 40 files"
            raise RuntimeError(message)
        documents_by_name = {}
        for descriptor in self.source["files"]:
            document = self._document_by_checksum(descriptor)
            if not document:
                document = self._upload_document(descriptor)
            document = self._document_by_checksum(descriptor)
            if len(document) != 1:
                raise RuntimeError(
                    f"B2C source document is missing after ingestion: "
                    f"{descriptor['source'].name}",
                )
            documents_by_name[descriptor["source"].name] = document

        evidence_records = self.env["b2c.provider.evidence"].sudo().search(
            [("company_id", "=", self.company.id)],
        )
        for evidence in evidence_records:
            document = documents_by_name.get(evidence.source_name)
            if not document or (
                document.checksum != evidence.source_checksum
                and not document.version_ids.filtered(
                    lambda version: version.checksum == evidence.source_checksum,
                )
            ):
                raise RuntimeError(
                    f"Provider evidence has no exact archived source: {evidence.evidence_key}",
                )
            evidence.with_context(b2c_evidence_import=True).write(
                {"archived_document_id": document.id},
            )

        orders = self.env["b2c.order"].sudo().search(
            [("company_id", "=", self.company.id)],
        )
        for order in orders:
            names = set(order.source_record_ids.evidence_id.mapped("source_name"))
            names.update(order.line_ids.evidence_id.mapped("source_name"))
            for name in names:
                self._link_document(documents_by_name[name], order)
            order.document_link_state = "verified" if names else "not_applicable"

        for model_name in ("b2c.payment.event", "b2c.fulfilment.event"):
            records = self.env[model_name].sudo().search(
                [("company_id", "=", self.company.id)],
            )
            for record in records.filtered("evidence_id"):
                self._link_document(
                    documents_by_name[record.evidence_id.source_name],
                    record,
                )

        for evidence in evidence_records.filtered("occurred_at"):
            provider = evidence.source_provider
            if provider in {"other", "manual"}:
                continue
            session = self._session(provider, evidence.occurred_at.date())
            if session:
                self._link_document(
                    documents_by_name[evidence.source_name],
                    session,
                )

        for descriptor in self.source["files"]:
            source_file = descriptor["source"]
            document = documents_by_name[source_file.name]
            if source_file.kind == "printful":
                sessions = self.env["b2c.accounting.session"].sudo().search(
                    [
                        ("company_id", "=", self.company.id),
                        ("source_provider", "=", "printful"),
                    ],
                )
                for session in sessions:
                    self._link_document(document, session)
            elif source_file.name == "sales-report 2025-1.pdf":
                self._link_document(
                    document,
                    self._session("revolut", date(2025, 12, 1), required=True),
                )
            elif source_file.name.startswith("Stripe Tax Invoice "):
                match = re.search(r"(20\d{2})-(\d{2})", source_file.name)
                if not match:
                    raise RuntimeError(f"Cannot derive Stripe invoice month: {source_file.name}")
                self._link_document(
                    document,
                    self._session(
                        "stripe",
                        date(int(match.group(1)), int(match.group(2)), 1),
                        required=True,
                    ),
                )

        b2c_models = {
            "b2c.order",
            "b2c.payment.event",
            "b2c.fulfilment.event",
            "b2c.accounting.session",
        }
        unlinked = [
            name
            for name, document in documents_by_name.items()
            if not document.link_ids.filtered(
                lambda link: link.active and link.res_model in b2c_models,
            )
        ]
        if unlinked:
            raise RuntimeError(
                f"B2C archive files have no durable B2C business link: {unlinked}",
            )
        archived_documents = self.env["usl.document"].sudo().browse(
            [document.id for document in documents_by_name.values()],
        )
        archived_documents._recompute_linked_record_access(sync_permissions=True)
        archived_documents.reconcile_linked_classification(limit=0)
        return {
            "archived_files": len(documents_by_name),
            "evidence_document_links": len(
                evidence_records.filtered("archived_document_id"),
            ),
        }

    def finalize(self, *, require_documents=False):
        moves, journal_fingerprint = self._critical_moves()
        bank_count = self._link_sessions_and_banks(moves)
        direct = self._link_direct_events(moves)
        dispositions = self._set_record_dispositions()
        document_statistics = (
            self._finalize_documents()
            if require_documents
            else {"archived_files": 0, "evidence_document_links": 0}
        )
        sessions = self.env["b2c.accounting.session"].sudo().search(
            [("company_id", "=", self.company.id)],
        )
        sessions.filtered(lambda session: session.state != "locked").action_refresh()
        pending = sum(
            self.env[model].sudo().search_count(
                [
                    ("company_id", "=", self.company.id),
                    ("accounting_link_state", "=", "pending"),
                ],
            )
            for model in (
                "b2c.order",
                "b2c.payment.event",
                "b2c.fulfilment.event",
            )
        )
        if pending:
            raise RuntimeError(f"B2C accounting disposition left {pending} records pending")
        return {
            "accounting_dispositions": dispositions,
            "bank_relationships": bank_count,
            "direct_events": len({item[0] for item in direct}),
            "direct_relationships": len(direct),
            "journal_fingerprint": {
                code: [count, str(debit), str(credit)]
                for code, (count, debit, credit) in journal_fingerprint.items()
            },
            "session_move_relationships": len(moves),
            **document_statistics,
        }
