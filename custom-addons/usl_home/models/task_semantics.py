"""Opt-in meaning-based suggestions for the existing task-description index."""
import math
import os
import re
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


def _embeddings(model, texts):
    """Only use the deployment's pinned local model. Never follow a redirect with text."""
    endpoint, name, digest = model
    try:
        with requests.Session() as client:
            client.trust_env = False
            tags = client.get(endpoint + '/api/tags', timeout=(1, 2), allow_redirects=False)
            if tags.status_code != 200 or not any(
                item.get('name') == name and item.get('digest', '').removeprefix('sha256:') == digest
                for item in tags.json()['models']
            ):
                return None
            response = client.post(endpoint + '/api/embed', json={'model': name, 'input': texts, 'truncate': False},
                                   timeout=(1, 5), allow_redirects=False)
            if response.status_code != 200:
                return None
            payload = response.json()
            vectors = payload.get('embeddings')
            if payload.get('model') != name or not isinstance(vectors, list) or len(vectors) != len(texts):
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
        model = _configured_model()
        if not model or not self._usl_has_vector_index():
            return
        digest = model[2]
        if not self.has_access('read') or not self.has_field_access(self._fields['description'], 'read'):
            return
        allowed = self.with_context(active_test=False)._search([]).select(SQL.identifier(self._table, 'id'))
        rows = self.env.execute_query(SQL('''
            SELECT task_id, left(content, %s) FROM usl_task_search_fragment f
             WHERE field_name = 'description' AND embedding_model IS DISTINCT FROM %s AND task_id IN (%s)
               AND NOT EXISTS (SELECT 1 FROM usl_task_search_queue q WHERE q.task_id = f.task_id)
             ORDER BY task_id LIMIT %s
        ''', TEXT_LIMIT, digest, allowed, BATCH_SIZE))
        if not rows:
            return
        # The lexical index folds accents. Use the original text for the language model.
        descriptions = self.browse([key for key, _text in rows]).read(['description'])
        texts = [' '.join(html2plaintext(row['description'] or '').split())[:TEXT_LIMIT] for row in descriptions]
        vectors = _embeddings(model, texts)
        if vectors is None:
            return
        # No row lock is held across the model call. A queued edit suppresses the old
        # version immediately, then the normal lexical refresh replaces the whole row.
        for (task_id, prefix), vector in zip(rows, vectors):
            self.env.cr.execute('''
                UPDATE usl_task_search_fragment f SET embedding = %s::vector, embedding_model = %s
                 WHERE task_id = %s AND field_name = 'description' AND left(content, %s) = %s
                   AND NOT EXISTS (SELECT 1 FROM usl_task_search_queue q WHERE q.task_id = f.task_id)
            ''', (str(vector), digest, task_id, TEXT_LIMIT, prefix))


    @api.model
    @api.readonly
    def search_by_meaning(self, query, limit=5):
        """Rank indexed task descriptions in the caller's selected companies by meaning."""
        home = self.env['usl.home.service']
        home._ensure_internal()
        query = home._search_arguments(query, limit, 0)
        Task = self.with_context(active_test=False)
        domain = company_domain(Task)
        group = {'key': 'task_meaning', 'label': self.env._('Related task descriptions'), 'records': []}
        result = {'group': group, 'status': 'empty'}
        if (not query or not Task.has_access('read') or not Task.has_field_access(Task._fields['description'], 'read')
                or not Task.has_field_access(Task._fields['name'], 'read')):
            return result
        model = _configured_model()
        if not model or not Task._usl_has_vector_index():
            result['status'] = 'unavailable'
            return result
        vectors = _embeddings(model, [query])
        if vectors is None:
            result['status'] = 'unavailable'
            return result
        # Rank *after* filtering by native record rules, company, source and freshness.
        # An inaccessible near neighbour must never displace a reader's own matches.
        allowed = Task._search(domain).select(SQL.identifier(Task._table, 'id'))
        ids = self.env.execute_query(SQL('''
            WITH eligible AS MATERIALIZED (
                SELECT f.task_id, f.embedding FROM usl_task_search_fragment f
                 WHERE f.field_name = 'description' AND f.embedding_model = %s AND f.embedding IS NOT NULL
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
