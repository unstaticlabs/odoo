"""Private PostgreSQL mechanics shared by three concrete content projections."""

from dataclasses import dataclass

from odoo import models
from odoo.fields import Domain
from odoo.tools import SQL, html2plaintext
from .search_words import folded_text, lexical_words, prepare_fragment


@dataclass(frozen=True)
class Projection:
    prefix: str
    id_column: str
    fields: tuple[str, ...]
    translated: bool = False

    @property
    def queue(self):
        return SQL.identifier(self.prefix + "_queue")

    @property
    def fragments(self):
        return SQL.identifier(self.prefix + "_fragment")

    @property
    def identity(self):
        return SQL.identifier(self.id_column)



class ContentIndexMixin(models.AbstractModel):
    """Python-inherited only: no registry model, table or public method.

    Keep the three concrete mutation targets literal so the existing action
    inventory sees every DDL/DML sink. Shared reads use native SQL identifiers.
    """

    _name = "usl.content.index.mixin"
    _register = False

    def _usl_validate_projection(self, projection):
        expected = {
            "project.task": "usl_task_search",
            "project.project": "usl_project_search",
            "res.partner": "usl_contact_search",
        }
        if expected.get(self._name) != projection.prefix:
            raise ValueError("Projection does not belong to this concrete model")


    def _usl_initialize_projection(self, projection):
        self._usl_validate_projection(projection)
        cr = self.env.cr
        if self._name == "project.task":
            cr.execute("""
                CREATE TABLE IF NOT EXISTS usl_task_search_queue (
                    task_id integer PRIMARY KEY REFERENCES project_task(id) ON DELETE CASCADE,
                    queued_at timestamp NOT NULL DEFAULT clock_timestamp()
                )
            """)
            cr.execute("""
                CREATE TABLE IF NOT EXISTS usl_task_search_fragment (
                    task_id integer NOT NULL REFERENCES project_task(id) ON DELETE CASCADE,
                    field_name varchar NOT NULL,
                    content text NOT NULL,
                    PRIMARY KEY (task_id, field_name)
                )
            """)
            cr.execute("ALTER TABLE usl_task_search_fragment ADD COLUMN IF NOT EXISTS words text[]")
            cr.execute("DROP INDEX IF EXISTS usl_task_search_fragment_lexical_idx")
            cr.execute("""
                CREATE INDEX IF NOT EXISTS usl_task_search_fragment_words_idx
                ON usl_task_search_fragment USING gin (words)
            """)
            if self.env.registry.has_trigram:
                cr.execute("""
                    CREATE INDEX IF NOT EXISTS usl_task_search_fragment_substring_idx
                    ON usl_task_search_fragment USING gin (content gin_trgm_ops)
                """)
            cr.execute("""
                INSERT INTO usl_task_search_queue (task_id)
                SELECT source.id FROM project_task source
                WHERE NOT EXISTS (SELECT 1 FROM usl_task_search_fragment fragment
                                  WHERE fragment.task_id = source.id AND fragment.words IS NOT NULL)
                ON CONFLICT (task_id) DO NOTHING
            """)
        elif self._name == "project.project":
            cr.execute("""
                CREATE TABLE IF NOT EXISTS usl_project_search_queue (
                    project_id integer PRIMARY KEY REFERENCES project_project(id) ON DELETE CASCADE,
                    queued_at timestamp NOT NULL DEFAULT clock_timestamp()
                )
            """)
            cr.execute("""
                CREATE TABLE IF NOT EXISTS usl_project_search_fragment (
                    project_id integer NOT NULL REFERENCES project_project(id) ON DELETE CASCADE,
                    field_name varchar NOT NULL,
                    content text NOT NULL, language varchar NOT NULL,
                    PRIMARY KEY (project_id, field_name, language)
                )
            """)
            cr.execute("ALTER TABLE usl_project_search_fragment ADD COLUMN IF NOT EXISTS words text[]")
            cr.execute("DROP INDEX IF EXISTS usl_project_search_fragment_lexical_idx")
            cr.execute("""
                CREATE INDEX IF NOT EXISTS usl_project_search_fragment_words_idx
                ON usl_project_search_fragment USING gin (words)
            """)
            if self.env.registry.has_trigram:
                cr.execute("""
                    CREATE INDEX IF NOT EXISTS usl_project_search_fragment_substring_idx
                    ON usl_project_search_fragment USING gin (content gin_trgm_ops)
                """)
            cr.execute("""
                INSERT INTO usl_project_search_queue (project_id)
                SELECT source.id FROM project_project source
                WHERE NOT EXISTS (SELECT 1 FROM usl_project_search_fragment fragment
                                  WHERE fragment.project_id = source.id AND fragment.words IS NOT NULL)
                ON CONFLICT (project_id) DO NOTHING
            """)
        elif self._name == "res.partner":
            cr.execute("""
                CREATE TABLE IF NOT EXISTS usl_contact_search_queue (
                    partner_id integer PRIMARY KEY REFERENCES res_partner(id) ON DELETE CASCADE,
                    queued_at timestamp NOT NULL DEFAULT clock_timestamp()
                )
            """)
            cr.execute("""
                CREATE TABLE IF NOT EXISTS usl_contact_search_fragment (
                    partner_id integer NOT NULL REFERENCES res_partner(id) ON DELETE CASCADE,
                    field_name varchar NOT NULL,
                    content text NOT NULL,
                    PRIMARY KEY (partner_id, field_name)
                )
            """)
            cr.execute("ALTER TABLE usl_contact_search_fragment ADD COLUMN IF NOT EXISTS words text[]")
            cr.execute("DROP INDEX IF EXISTS usl_contact_search_fragment_lexical_idx")
            cr.execute("""
                CREATE INDEX IF NOT EXISTS usl_contact_search_fragment_words_idx
                ON usl_contact_search_fragment USING gin (words)
            """)
            if self.env.registry.has_trigram:
                cr.execute("""
                    CREATE INDEX IF NOT EXISTS usl_contact_search_fragment_substring_idx
                    ON usl_contact_search_fragment USING gin (content gin_trgm_ops)
                """)
            cr.execute("""
                INSERT INTO usl_contact_search_queue (partner_id)
                SELECT source.id FROM res_partner source
                WHERE NOT EXISTS (SELECT 1 FROM usl_contact_search_fragment fragment
                                  WHERE fragment.partner_id = source.id AND fragment.words IS NOT NULL)
                ON CONFLICT (partner_id) DO NOTHING
            """)

    def _usl_queue_projection(self, projection, *, all_records=False):
        self._usl_validate_projection(projection)
        if not all_records and not self:
            return
        cr = self.env.cr
        if self._name == "project.task":
            cr.execute("""
                INSERT INTO usl_task_search_queue (task_id)
                SELECT id FROM project_task WHERE %s OR id = ANY(%s)
                ON CONFLICT (task_id) DO UPDATE
                SET queued_at = usl_task_search_queue.queued_at
            """, (all_records, self.ids))
        elif self._name == "project.project":
            cr.execute("""
                INSERT INTO usl_project_search_queue (project_id)
                SELECT id FROM project_project WHERE %s OR id = ANY(%s)
                ON CONFLICT (project_id) DO UPDATE
                SET queued_at = usl_project_search_queue.queued_at
            """, (all_records, self.ids))
        elif self._name == "res.partner":
            cr.execute("""
                INSERT INTO usl_contact_search_queue (partner_id)
                SELECT id FROM res_partner WHERE %s OR id = ANY(%s)
                ON CONFLICT (partner_id) DO UPDATE
                SET queued_at = usl_contact_search_queue.queued_at
            """, (all_records, self.ids))

    def _usl_delete_fragments(self, projection, ids):
        self._usl_validate_projection(projection)
        if self._name == "project.task":
            self.env.cr.execute("DELETE FROM usl_task_search_fragment WHERE task_id = ANY(%s)", (ids,))
        elif self._name == "project.project":
            self.env.cr.execute("DELETE FROM usl_project_search_fragment WHERE project_id = ANY(%s)", (ids,))
        elif self._name == "res.partner":
            self.env.cr.execute("DELETE FROM usl_contact_search_fragment WHERE partner_id = ANY(%s)", (ids,))

    def _usl_remove_queue(self, projection, ids):
        self._usl_validate_projection(projection)
        if self._name == "project.task":
            self.env.cr.execute("DELETE FROM usl_task_search_queue WHERE task_id = ANY(%s)", (ids,))
        elif self._name == "project.project":
            self.env.cr.execute("DELETE FROM usl_project_search_queue WHERE project_id = ANY(%s)", (ids,))
        elif self._name == "res.partner":
            self.env.cr.execute("DELETE FROM usl_contact_search_queue WHERE partner_id = ANY(%s)", (ids,))

    def _usl_insert_fragment(self, projection, record_id, name, content, language):
        self._usl_validate_projection(projection)
        folded, words = prepare_fragment(self.env, content)
        if self._name == "project.task":
            self.env.cr.execute("""
                INSERT INTO usl_task_search_fragment (task_id, field_name, content, words)
                VALUES (%s, %s, %s, %s)
            """, (record_id, name, folded, words))
        elif self._name == "project.project":
            self.env.cr.execute("""
                INSERT INTO usl_project_search_fragment (project_id, field_name, content, language, words)
                VALUES (%s, %s, %s, %s, %s)
            """, (record_id, name, folded, language, words))
        elif self._name == "res.partner":
            self.env.cr.execute("""
                INSERT INTO usl_contact_search_fragment (partner_id, field_name, content, words)
                VALUES (%s, %s, %s, %s)
            """, (record_id, name, folded, words))

    def _usl_refresh_projection(self, projection):
        """Publish a bounded batch under the queue locks in the caller transaction."""
        self._usl_validate_projection(projection)
        self.flush_model(list(projection.fields))
        cr = self.env.cr
        cr.execute(SQL("""
            SELECT %s FROM %s ORDER BY queued_at, %s
            LIMIT 500 FOR UPDATE SKIP LOCKED
        """, projection.identity, projection.queue, projection.identity))
        ids = [row[0] for row in cr.fetchall()]
        if not ids:
            return
        cr.execute(SQL("SELECT id, %s FROM %s WHERE id = ANY(%s)",
            SQL(", ").join(SQL.identifier(name) for name in projection.fields),
            SQL.identifier(self._table), ids))
        rows = cr.fetchall()
        self._usl_delete_fragments(projection, ids)
        for record_id, *values in rows:
            for name, value in zip(projection.fields, values):
                translations = value.items() if self._fields[name].translate and isinstance(value, dict) else [("", value)]
                for language, translated_value in translations:
                    text = html2plaintext(translated_value or "") if self._fields[name].type == "html" else translated_value or ""
                    content = " ".join(text.split())
                    # An empty explicit translation must still suppress en_US.
                    if not content and not language:
                        continue
                    self._usl_insert_fragment(projection, record_id, name, content, language)
        self._usl_remove_queue(projection, ids)

    def _usl_indexed_projection_domain(self, projection, text):
        self._usl_validate_projection(projection)
        fields = tuple(name for name in projection.fields
                       if self.has_field_access(self._fields[name], "read"))
        if not fields or not text or not self.has_access("read"):
            return Domain.FALSE
        self.flush_model(list(projection.fields))
        language = SQL("")
        if projection.translated:
            current = self.env.lang or "en_US"
            language = SQL("""AND (
                fragment.language = '' OR fragment.language = %s OR (
                    fragment.language = 'en_US' AND NOT EXISTS (
                        SELECT 1 FROM %s translated WHERE translated.%s = fragment.%s
                        AND translated.field_name = fragment.field_name AND translated.language = %s
                    )
                )
            )""", current, projection.fragments, projection.identity, projection.identity, current)
        indexed = Domain("id", "in", SQL("""
            WITH needle AS MATERIALIZED (SELECT %s AS text),
                 query AS MATERIALIZED (SELECT text, %s AS words FROM needle)
            SELECT fragment.%s FROM %s fragment, query
            WHERE fragment.field_name IN %s %s
              AND NOT EXISTS (SELECT 1 FROM %s pending WHERE pending.%s = fragment.%s)
              AND ((cardinality(query.words) > 0 AND fragment.words @> query.words)
                   OR fragment.content ILIKE ('%%' || query.text || '%%'))
        """, folded_text(self.env.registry, text), lexical_words(SQL("needle.text")),
            projection.identity, projection.fragments, fields, language,
            projection.queue, projection.identity, projection.identity))
        pending = Domain("id", "in", SQL("SELECT %s FROM %s", projection.identity, projection.queue))
        live = Domain.OR([Domain(name, "ilike", text) for name in fields])
        return indexed | (pending & live)
