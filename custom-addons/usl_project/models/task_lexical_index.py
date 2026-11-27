"""Private, rebuildable PostgreSQL projection of task title and description.

Fragments are separate by source field. Only the native task domain is exposed;
neither projection data nor its work queue has a public model or RPC surface.
"""

from odoo import api, models
from odoo.fields import Domain
from odoo.tools import SQL, html2plaintext
from odoo.tools.misc import remove_accents


INDEXED_FIELDS = ("name", "description")


class ProjectTask(models.Model):
    _inherit = "project.task"

    @api.private
    def init(self):
        super().init()
        cr = self.env.cr
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
        cr.execute("""
            CREATE INDEX IF NOT EXISTS usl_task_search_fragment_lexical_idx
            ON usl_task_search_fragment USING gin (to_tsvector('simple', content))
        """)
        if self.env.registry.has_trigram:
            cr.execute("""
                CREATE INDEX IF NOT EXISTS usl_task_search_fragment_substring_idx
                ON usl_task_search_fragment USING gin (content gin_trgm_ops)
            """)
        # Backfill only missing projections. A repeat upgrade must not hide
        # ready lexical results while unchanged sources wait to be rebuilt.
        cr.execute("""
            INSERT INTO usl_task_search_queue (task_id)
            SELECT task.id FROM project_task task
            WHERE NOT EXISTS (
                SELECT 1 FROM usl_task_search_fragment fragment
                WHERE fragment.task_id = task.id
            ) ON CONFLICT (task_id) DO NOTHING
        """)

    def _usl_queue_task_content(self, *, all_tasks=False):
        if all_tasks:
            self.env.cr.execute("""
                INSERT INTO usl_task_search_queue (task_id)
                SELECT id FROM project_task ON CONFLICT (task_id)
                DO UPDATE SET queued_at = usl_task_search_queue.queued_at
            """)
        elif self:
            self.env.cr.execute("""
                INSERT INTO usl_task_search_queue (task_id)
                SELECT unnest(%s::integer[]) ON CONFLICT (task_id)
                DO UPDATE SET queued_at = usl_task_search_queue.queued_at
            """, (self.ids,))

    @api.model_create_multi
    def create(self, vals_list):
        records = super().create(vals_list)
        records._usl_queue_task_content()
        return records

    def write(self, vals):
        result = super().write(vals)
        if any(name in vals for name in INDEXED_FIELDS):
            self._usl_queue_task_content()
        return result

    @api.model
    def _cron_usl_index_task_content(self):
        """Consume one bounded batch; concurrent workers take disjoint tasks.

        The queue lock is retained through publication and deletion. A task
        write that races this worker enqueues again before its own commit, so
        an older fragment can never become authoritative for newer content.
        """
        self.flush_model(list(INDEXED_FIELDS))
        cr = self.env.cr
        cr.execute("""
            SELECT task_id FROM usl_task_search_queue
            ORDER BY queued_at, task_id LIMIT 500 FOR UPDATE SKIP LOCKED
        """)
        ids = [row[0] for row in cr.fetchall()]
        if not ids:
            return
        # Fixed source fields only. This private maintenance read is not an
        # authorization query; search applies current field and task access.
        cr.execute("SELECT id, name, description FROM project_task WHERE id = ANY(%s)", (ids,))
        rows = cr.fetchall()
        cr.execute("DELETE FROM usl_task_search_fragment WHERE task_id = ANY(%s)", (ids,))
        for task_id, name, description in rows:
            for field_name, value in zip(INDEXED_FIELDS, (name, description)):
                text = html2plaintext(value or "") if field_name == "description" else value or ""
                content = remove_accents(" ".join(text.split()))
                if content:
                    cr.execute("""
                        INSERT INTO usl_task_search_fragment (task_id, field_name, content)
                        VALUES (%s, %s, %s)
                    """, (task_id, field_name, content))
        cr.execute("DELETE FROM usl_task_search_queue WHERE task_id = ANY(%s)", (ids,))

    @api.model
    def _usl_indexed_task_content_domain(self, text):
        fields = tuple(name for name in INDEXED_FIELDS
                       if self.has_field_access(self._fields[name], "read"))
        if not fields or not text or not self.has_access("read"):
            return Domain.FALSE
        self.flush_model(list(INDEXED_FIELDS))
        live_text = text
        text = remove_accents(text)
        indexed = Domain("id", "in", SQL("""
            SELECT fragment.task_id FROM usl_task_search_fragment fragment
            WHERE fragment.field_name IN %s
              AND NOT EXISTS (
                  SELECT 1 FROM usl_task_search_queue pending
                  WHERE pending.task_id = fragment.task_id
              )
              AND (to_tsvector('simple', fragment.content) @@ plainto_tsquery('simple', %s)
                   OR fragment.content ILIKE %s)
        """, fields, text, "%%" + text + "%%"))
        # Pending records remain available through the existing live contains
        # search. No search request mutates or refreshes the projection.
        pending = Domain("id", "in", SQL("SELECT task_id FROM usl_task_search_queue"))
        live = Domain.OR([Domain(name, "ilike", live_text) for name in fields])
        return indexed | (pending & live)
