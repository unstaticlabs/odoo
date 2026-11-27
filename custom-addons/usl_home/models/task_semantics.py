"""Opt-in meaning-based suggestions for the existing task-description index."""
import math
import os
import re
import time
from urllib.parse import urlsplit

import requests

from odoo import api, models
from odoo.tools import SQL, html2plaintext

from odoo.addons.usl_project.models.content_search import company_domain

from .content_search import _excerpt


DIMENSIONS = 1024
TEXT_LIMIT = 2048
BATCH_SIZE = 16
# Calibrated with French/English task paraphrases for BGE-M3. A high limit should
# not fill the screen with unrelated tasks after one clear answer.
MIN_SIMILARITY = 0.40
NEAR_BEST_GAP = 0.06
# A person is waiting on the interactive path: give the model one second and a
# half in all, then fall back to keyword results without a word.
INTERACTIVE_BUDGET = 1.5
# The pinned digest is verified again after this long, not on every keystroke.
ATTESTATION_SECONDS = 300
# (endpoint, model, digest) -> monotonic time until which the attestation holds.
_ATTESTED = {}


def _configured_model():
    if os.getenv('USL_TASK_SEMANTIC_SEARCH') != '1':
        return None
    endpoint = os.getenv('PAPERLESS_AI_LLM_EMBEDDING_ENDPOINT', '').rstrip('/')
    name = os.getenv('PAPERLESS_AI_LLM_EMBEDDING_MODEL', '')
    digest = os.getenv('USL_OLLAMA_MANIFEST_SHA256', '').removeprefix('sha256:')
    try:
        url = urlsplit(endpoint)
    except ValueError:
        return None
    if (url.scheme not in ('http', 'https') or not url.hostname or url.username or url.password
            or url.query or url.fragment or not name or not re.fullmatch('[a-f0-9]{64}', digest)):
        return None
    return endpoint, name, digest


def _embeddings(model, texts, *, interactive=False):
    """Only use the deployment's pinned local model. Never follow a redirect with text.

    The pinned digest is attested through /api/tags and remembered for a few
    minutes in this worker, so a search costs one HTTP call, not two. The
    interactive path has a hard total budget; the background indexer is patient.
    """
    endpoint, name, digest = model
    started = time.monotonic()

    def left(cap):
        if not interactive:
            return cap
        return max(0.05, min(cap, INTERACTIVE_BUDGET - (time.monotonic() - started)))

    try:
        with requests.Session() as client:
            client.trust_env = False
            if _ATTESTED.get(model, 0) <= time.monotonic():
                tags = client.get(endpoint + '/api/tags', timeout=(left(1), left(2)), allow_redirects=False)
                if tags.status_code != 200 or not any(
                    item.get('name') == name and item.get('digest', '').removeprefix('sha256:') == digest
                    for item in tags.json()['models']
                ):
                    _ATTESTED.pop(model, None)
                    return None
                _ATTESTED[model] = time.monotonic() + ATTESTATION_SECONDS
            response = client.post(endpoint + '/api/embed', json={'model': name, 'input': texts, 'truncate': False},
                                   timeout=(left(1), left(5)), allow_redirects=False)
            if response.status_code != 200:
                return None
            payload = response.json()
            vectors = payload.get('embeddings')
            if payload.get('model') != name or not isinstance(vectors, list) or len(vectors) != len(texts):
                # The server no longer answers as the pinned model.
                _ATTESTED.pop(model, None)
                return None
            for vector in vectors:
                if (not isinstance(vector, list) or len(vector) != DIMENSIONS or not any(vector)
                        or not all(type(value) in (int, float) and math.isfinite(value)
                                   and abs(value) <= 1e30 for value in vector)):
                    return None
            return vectors
    except (requests.RequestException, ValueError, KeyError, TypeError, AttributeError, OverflowError):
        return None


class ProjectTask(models.Model):
    _inherit = 'project.task'

    @api.private
    def init(self):
        super().init()
        self.env.cr.execute("SELECT to_regtype('vector') IS NOT NULL")
        if self.env.cr.fetchone()[0]:
            self.env.cr.execute('''
                ALTER TABLE usl_task_search_fragment
                ADD COLUMN IF NOT EXISTS embedding vector(1024), ADD COLUMN IF NOT EXISTS embedding_model text
            ''')

    @api.model
    def _usl_has_vector_index(self):
        self.env.cr.execute('''
            SELECT EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = 'usl_task_search_fragment'::regclass
                           AND attname = 'embedding' AND NOT attisdropped AND atttypid = to_regtype('vector'))
        ''')
        return self.env.cr.fetchone()[0]

    @api.model
    def _cron_usl_embed_task_descriptions(self):
        """Embed each task's title and description, stored on its title fragment.

        Every task has a title, so a task without a description is found by
        meaning too. The vector lives on the ``name`` fragment row and goes away
        with it whenever the lexical indexer rebuilds the task.
        """
        model = _configured_model()
        if not model or not self._usl_has_vector_index():
            return
        digest = model[2]
        if (not self.has_access('read') or not self.has_field_access(self._fields['description'], 'read')
                or not self.has_field_access(self._fields['name'], 'read')):
            return
        allowed = self.with_context(active_test=False)._search([]).select(SQL.identifier(self._table, 'id'))
        rows = self.env.execute_query(SQL('''
            SELECT task_id, left(content, %s) FROM usl_task_search_fragment f
             WHERE field_name = 'name' AND embedding_model IS DISTINCT FROM %s AND task_id IN (%s)
               AND NOT EXISTS (SELECT 1 FROM usl_task_search_queue q WHERE q.task_id = f.task_id)
             ORDER BY task_id LIMIT %s
        ''', TEXT_LIMIT, digest, allowed, BATCH_SIZE))
        if not rows:
            return
        # The lexical index folds accents. Use the original text for the language model.
        tasks = self.browse([key for key, _text in rows]).read(['name', 'description'])
        texts = [self._usl_embedding_text(row['name'], row['description']) for row in tasks]
        vectors = _embeddings(model, texts)
        if vectors is None:
            return
        # No row lock is held across the model call. A queued edit suppresses the old
        # version immediately, then the normal lexical refresh replaces the whole row.
        for (task_id, prefix), vector in zip(rows, vectors):
            self.env.cr.execute('''
                UPDATE usl_task_search_fragment f SET embedding = %s::vector, embedding_model = %s
                 WHERE task_id = %s AND field_name = 'name' AND left(content, %s) = %s
                   AND NOT EXISTS (SELECT 1 FROM usl_task_search_queue q WHERE q.task_id = f.task_id)
            ''', (str(vector), digest, task_id, TEXT_LIMIT, prefix))

    @api.model
    def _usl_embedding_text(self, name, description):
        title = ' '.join((name or '').split())
        body = ' '.join(html2plaintext(description or '').split())
        return (title + '\n\n' + body if body else title)[:TEXT_LIMIT]

    @api.model
    @api.readonly
    def search_by_meaning(self, query, limit=5):
        """Rank tasks by the meaning of their title and description, in the caller's selected companies.

        ``disabled`` means the deployment does not offer this search, so a client
        can stop asking; ``unavailable`` means the model did not answer this time.
        """
        home = self.env['usl.home.service']
        home._ensure_internal()
        query = home._search_arguments(query, limit, 0)
        Task = self.with_context(active_test=False)
        domain = company_domain(Task)
        group = {'key': 'task_meaning', 'label': self.env._('Related by subject'), 'records': []}
        result = {'group': group, 'status': 'empty'}
        if (not query or not Task.has_access('read') or not Task.has_field_access(Task._fields['description'], 'read')
                or not Task.has_field_access(Task._fields['name'], 'read')):
            return result
        model = _configured_model()
        if not model or not Task._usl_has_vector_index():
            result['status'] = 'disabled'
            return result
        vectors = _embeddings(model, [query], interactive=True)
        if vectors is None:
            result['status'] = 'unavailable'
            return result
        # Rank *after* filtering by native record rules, company, source and freshness.
        # An inaccessible near neighbour must never displace a reader's own matches.
        allowed = Task._search(domain).select(SQL.identifier(Task._table, 'id'))
        ids = self.env.execute_query(SQL('''
            WITH eligible AS MATERIALIZED (
                SELECT f.task_id, f.embedding FROM usl_task_search_fragment f
                 WHERE f.field_name = 'name' AND f.embedding_model = %s AND f.embedding IS NOT NULL
                   AND f.task_id IN (%s)
                   AND NOT EXISTS (SELECT 1 FROM usl_task_search_queue q WHERE q.task_id = f.task_id)
            ), nearest AS MATERIALIZED (
                SELECT task_id, embedding <=> %s::vector AS distance FROM eligible
                ORDER BY distance, task_id LIMIT %s
            )
            SELECT task_id FROM nearest
             WHERE distance <= %s AND distance <= (SELECT min(distance) FROM nearest) + %s
             ORDER BY distance, task_id
        ''', model[2], allowed, str(vectors[0]), limit, 1 - MIN_SIMILARITY, NEAR_BEST_GAP))
        for record in Task.browse([record_id for record_id, in ids]):
            group['records'].append({'model': Task._name, 'id': record.id, 'title': record.display_name,
                                     'excerpt': _excerpt(record.description, query), 'context_label': home._search_context_label(record)})
        if group['records']:
            result['status'] = 'ok'
        return result
