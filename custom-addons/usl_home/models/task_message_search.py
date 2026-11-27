"""Derived task-message text, always queried through current native mail access."""

import re

from odoo import api, fields, models
from odoo.exceptions import ValidationError
from odoo.fields import Domain
from odoo.tools import SQL, html2plaintext
from odoo.tools.misc import remove_accents

from odoo.addons.usl_project.models.content_search import company_domain
from odoo.addons.usl_project.models.search_words import folded_text, lexical_words, prepare_fragment


def _query_text(value, env):
    if value is None:
        return ""
    if not isinstance(value, str) or len(value) > 2048:
        raise ValidationError(env._("Enter a search of up to 2,048 characters."))
    return value.strip()


def _source_excerpt(env, value, query):
    plain = " ".join(html2plaintext(value or "").split())
    if len(plain) <= 240:
        return plain
    # Keep positions in the original string. Folding a character such as œ
    # can expand its length, so a position in folded text cannot slice plain.
    words = list(re.finditer(r"\w+", plain))
    terms = re.findall(r"\w+", query)
    folded_terms = [remove_accents(term).casefold() for term in terms]
    hit = None
    for word in words:
        folded = remove_accents(word.group()).casefold()
        if any(term in folded for term in folded_terms):
            hit = word
            break
    if hit is None and words and terms and env.registry.has_unaccent:
        env.cr.execute("""
            SELECT pos FROM unnest(%s::text[]) WITH ORDINALITY AS source(word, pos)
            WHERE EXISTS (
                SELECT 1 FROM unnest(%s::text[]) AS query(term)
                WHERE strpos(lower(unaccent(word)), lower(unaccent(term))) > 0
            )
            ORDER BY pos LIMIT 1
        """, ([word.group() for word in words], terms))
        row = env.cr.fetchone()
        if row:
            hit = words[row[0] - 1]
    start = max(0, (hit.start() if hit else 0) - 65)
    return ("…" if start else "") + plain[start:start + 240] + ("…" if len(plain) > start + 240 else "")


class MailMessage(models.Model):
    _inherit = "mail.message"

    @api.private
    def init(self):
        super().init()
        self.env.cr.execute("""
            CREATE TABLE IF NOT EXISTS usl_task_message_search_queue (
                message_id integer PRIMARY KEY REFERENCES mail_message(id) ON DELETE CASCADE,
                queued_at timestamp NOT NULL DEFAULT clock_timestamp()
            )
        """)
        self.env.cr.execute("""
            CREATE TABLE IF NOT EXISTS usl_task_message_search_fragment (
                message_id integer NOT NULL REFERENCES mail_message(id) ON DELETE CASCADE,
                field_name varchar NOT NULL, content text NOT NULL,
                PRIMARY KEY (message_id, field_name)
            )
        """)
        self.env.cr.execute("""
            ALTER TABLE usl_task_message_search_fragment ADD COLUMN IF NOT EXISTS words text[]
        """)
        self.env.cr.execute("DROP INDEX IF EXISTS usl_task_message_search_lexical_idx")
        self.env.cr.execute("""
            CREATE INDEX IF NOT EXISTS usl_task_message_search_words_idx
            ON usl_task_message_search_fragment USING gin (words)
        """)
        if self.env.registry.has_trigram:
            self.env.cr.execute("""
                CREATE INDEX IF NOT EXISTS usl_task_message_search_substring_idx
                ON usl_task_message_search_fragment USING gin (content gin_trgm_ops)
            """)
        self.env.cr.execute("""
            INSERT INTO usl_task_message_search_queue (message_id)
            SELECT source.id FROM mail_message source WHERE source.model = 'project.task'
              AND NOT EXISTS (SELECT 1 FROM usl_task_message_search_fragment fragment
                              WHERE fragment.message_id = source.id AND fragment.words IS NOT NULL)
            ON CONFLICT (message_id) DO NOTHING
        """)

    def _usl_queue_task_messages(self, *, all_records=False):
        if not all_records and not self:
            return
        self.env.cr.execute("""
            INSERT INTO usl_task_message_search_queue (message_id)
            SELECT source.id FROM mail_message source
            WHERE (%s OR source.id = ANY(%s)) AND (
                source.model = 'project.task' OR EXISTS (
                    SELECT 1 FROM usl_task_message_search_fragment fragment
                    WHERE fragment.message_id = source.id
                ) OR EXISTS (
                    SELECT 1 FROM usl_task_message_search_queue pending
                    WHERE pending.message_id = source.id
                )
            ) ON CONFLICT (message_id) DO UPDATE
              SET queued_at = usl_task_message_search_queue.queued_at
        """, (all_records, self.ids))
        if self.env.cr.rowcount:
            self.env["project.task"].invalidate_model(["usl_search_sources"])

    @api.model_create_multi
    def create(self, vals_list):
        messages = super().create(vals_list)
        messages._usl_queue_task_messages()
        return messages

    def write(self, vals):
        result = super().write(vals)
        if any(name in vals for name in ("body", "subject", "model", "res_id", "message_type", "subtype_id")):
            self._usl_queue_task_messages()
        return result

    @api.model
    def _cron_usl_index_task_messages(self):
        self.flush_model(["body", "subject", "model"])
        cr = self.env.cr
        cr.execute("""
            SELECT message_id FROM usl_task_message_search_queue
            ORDER BY queued_at, message_id LIMIT 500 FOR UPDATE SKIP LOCKED
        """)
        ids = [row[0] for row in cr.fetchall()]
        if not ids:
            return
        cr.execute("SELECT id, subject, body FROM mail_message WHERE id = ANY(%s) AND model = 'project.task'", (ids,))
        rows = cr.fetchall()
        cr.execute("DELETE FROM usl_task_message_search_fragment WHERE message_id = ANY(%s)", (ids,))
        for message_id, subject, body in rows:
            for name, value in (("subject", subject), ("body", html2plaintext(body or ""))):
                content, words = prepare_fragment(self.env, " ".join((value or "").split()))
                cr.execute("""
                    INSERT INTO usl_task_message_search_fragment (message_id, field_name, content, words)
                    VALUES (%s, %s, %s, %s)
                """, (message_id, name, content, words))
        cr.execute("DELETE FROM usl_task_message_search_queue WHERE message_id = ANY(%s)", (ids,))
        self.env["project.task"].invalidate_model(["usl_search_sources"])

    def _usl_readable_task_message_fields(self):
        if not self.has_access("read"):
            return ()
        return tuple(name for name in ("body", "subject") if self.has_field_access(self._fields[name], "read"))

    def _usl_task_conversation_domain(self):
        # Native task events are notification messages with a task-specific
        # subtype. Notes/comments keep their native shared subtypes, including
        # API-authored notes whose message_type defaults to notification.
        task_event = (Domain("message_type", "=", "notification")
                      & Domain("subtype_id", "!=", False)
                      & Domain("subtype_id", "in", SQL(
                          "SELECT id FROM mail_message_subtype WHERE res_model = 'project.task'"
                      )))
        return (Domain("model", "=", "project.task")
                & Domain("message_type", "not in", ("user_notification", "tracking"))
                & ~task_event)

    def _usl_matching_task_message_query(self, query, *, task_ids=None, source_fields=None):
        text = str(query or "").strip()
        readable = self._usl_readable_task_message_fields()
        names = tuple(name for name in readable if source_fields is None or name in source_fields)
        domain = self._usl_task_conversation_domain()
        if not text or not names:
            return self._search(domain & Domain.FALSE)
        self.flush_model(["body", "subject", "model", "res_id", "message_type"])
        indexed = Domain("id", "in", SQL("""
            WITH needle AS MATERIALIZED (SELECT %s AS text),
                 query AS MATERIALIZED (SELECT text, %s AS words FROM needle)
            SELECT fragment.message_id FROM usl_task_message_search_fragment fragment, query
            WHERE fragment.field_name IN %s
              AND NOT EXISTS (SELECT 1 FROM usl_task_message_search_queue pending
                              WHERE pending.message_id = fragment.message_id)
              AND ((cardinality(query.words) > 0 AND fragment.words @> query.words)
                   OR fragment.content ILIKE ('%%' || query.text || '%%'))
        """, folded_text(self.env.registry, text), lexical_words(SQL("needle.text")), names))
        pending = Domain("id", "in", SQL("SELECT message_id FROM usl_task_message_search_queue"))
        live = Domain.OR([Domain(name, "ilike", text) for name in names])
        if task_ids is not None:
            domain &= Domain("res_id", "in", task_ids)
        # An explicit single model selects Odoo's native SQL mail-access path,
        # preserving its author/recipient/parent semantics without the general10k scan.
        return self._search(domain & (indexed | (pending & live)))


class ProjectTask(models.Model):
    _inherit = "project.task"

    usl_search_sources = fields.Json(
        string="Search sources", compute="_compute_usl_search_sources",
        compute_sudo=False, readonly=True,
        help="Readable chatter evidence for the explicit search_source_query context.",
    )

    @api.model
    def _search_usl_search_text(self, operator, value):
        base = super()._search_usl_search_text(operator, value)
        if not self.has_access("read") or not self.has_field_access(self._fields["message_ids"], "read"):
            return base
        query = self.env["mail.message"]._usl_matching_task_message_query(value)
        messages = Domain("id", "in", query.select(query.table.res_id))
        return base & ~messages if operator.startswith("not ") else company_domain(self) & (base | messages)

    def _usl_matching_message_sources(self, query):
        text = _query_text(query, self.env)
        if not text or not self.has_access("read") or not self.has_field_access(self._fields["message_ids"], "read"):
            return {}
        ids = [record.id for record in self if type(record.id) is int]
        readable = self.with_context(active_test=False).search(company_domain(self) & Domain("id", "in", ids))
        Message = self.env["mail.message"]
        result = {}
        for name in Message._usl_readable_task_message_fields():
            remaining = [record.id for record in readable if record.id not in result]
            if not remaining:
                break
            native_query = Message._usl_matching_task_message_query(text, task_ids=remaining, source_fields=(name,))
            self.env.cr.execute(SQL("""
                SELECT DISTINCT ON (authorized.res_id) authorized.id
                FROM (%s) authorized ORDER BY authorized.res_id, authorized.id DESC
            """, native_query.select(native_query.table.id, native_query.table.res_id)))
            messages = Message.browse([row[0] for row in self.env.cr.fetchall()])
            for message in messages:
                result[message.res_id] = {
                    "kind": "message", "model": "mail.message", "id": message.id,
                    "field": name, "label": self.env._("Chatter message"),
                    "excerpt": _source_excerpt(self.env, message[name] or "", text),
                    "url": "/mail/message/%s" % message.id,
                }
        return result

    @api.depends("company_id", "active", "message_ids", "message_ids.body", "message_ids.subject", "message_ids.model", "message_ids.res_id", "message_ids.message_type", "message_ids.subtype_id")
    @api.depends_context("uid", "lang", "company", "allowed_company_ids", "search_source_query")
    def _compute_usl_search_sources(self):
        sources = self._usl_matching_message_sources(self.env.context.get("search_source_query"))
        for task in self:
            source = sources.get(task.id)
            task.usl_search_sources = [source] if source else []
