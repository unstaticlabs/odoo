"""Prove native source-write/index publication concurrency on disposable QA data.

Run through Odoo shell with usl_project and usl_home installed. The fixed
SEARCH_QA_MODEL selector supports project.task (default), project.project and
res.partner. The script
commits its own synthetic fixtures, uses Odoo's normal transaction retry boundary,
and removes those fixtures afterwards. It never targets a production database.
"""

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from odoo import api, SUPERUSER_ID
from odoo.http.retrying import retrying
from odoo.tools import SQL

if not env.cr.dbname.startswith("odoo_search_3758"):
    raise RuntimeError("Use a disposable odoo_search_3758 QA database only")

registry = env.registry
model_name = os.environ.get('SEARCH_QA_MODEL', 'project.task')
configuration = {
    'project.task': ('description', 'project_task', 'usl_task_search_queue', 'task_id', '_cron_usl_index_task_content'),
    'project.project': ('description', 'project_project', 'usl_project_search_queue', 'project_id', '_cron_usl_index_content'),
    'res.partner': ('comment', 'res_partner', 'usl_contact_search_queue', 'partner_id', '_cron_usl_index_content'),
}
if model_name not in configuration:
    raise RuntimeError('Choose one of the three concrete search projection models')
field_name, source_table, queue_table, id_column, worker_method = configuration[model_name]
Source = env[model_name]
project = env['project.project']
values = {'name': '[QA] Queue race', field_name: 'Amber heliograph earlier'}
if model_name == 'project.task':
    project = project.create({'name': '[QA] Search concurrency'})
    values['project_id'] = project.id
record = Source.with_context(tracking_disable=True).create(values)
record_id = record.id
getattr(Source, worker_method)()
env.cr.commit()
record.with_context(tracking_disable=True).write({field_name: 'Amber heliograph snapshot'})
env.cr.commit()
ready, writer_ready, resume = threading.Event(), threading.Event(), threading.Event()
writer_pid = []

def worker():
    with registry.cursor() as cr:
        model = api.Environment(cr, SUPERUSER_ID, {})[model_name]
        execute = cr.execute
        def observe(query, *args, **kwargs):
            result = execute(query, *args, **kwargs)
            code = ' '.join((query._sql_tuple[0] if isinstance(query, SQL) else query).replace(chr(34), '').split())
            if code.startswith('SELECT id, name, ' + field_name + ' FROM ' + source_table):
                ready.set()
                assert resume.wait(20), 'Index worker release timed out'
            return result
        with patch.object(cr, 'execute', observe):
            getattr(model, worker_method)()
        cr.commit()

def writer():
    with registry.cursor() as cr:
        model = api.Environment(cr, SUPERUSER_ID, {})[model_name]
        cr.execute('SELECT pg_backend_pid()')
        writer_pid.append(cr.fetchone()[0])
        execute = cr.execute
        def observe(query, *args, **kwargs):
            code = ' '.join((query._sql_tuple[0] if isinstance(query, SQL) else query).replace(chr(34), '').split())
            if 'INSERT INTO ' + queue_table in code:
                writer_ready.set()
            return execute(query, *args, **kwargs)
        with patch.object(cr, 'execute', observe):
            retrying(lambda: model.browse(record_id).with_context(tracking_disable=True).write({field_name: 'Violet semaphore replacement'}), model.env)

try:
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(worker)
        if not ready.wait(20):
            first.result(timeout=1)
            raise AssertionError('Worker never reached the actual native source snapshot')
        second = pool.submit(writer)
        try:
            assert writer_ready.wait(20), 'Native source write never reached invalidation'
            blocked = False
            for _ in range(50):
                env.cr.execute('SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s', (writer_pid[0],))
                row = env.cr.fetchone()
                if row and row[0] == 'Lock':
                    blocked = True
                    break
                time.sleep(.02)
            assert blocked and not second.done(), 'Concurrent invalidation bypassed the index worker lock'
        finally:
            resume.set()
        first.result(timeout=20)
        second.result(timeout=20)
    env.cr.rollback()
    env.invalidate_all()
    env.cr.execute(SQL('SELECT count(*) FROM %s WHERE %s = %s', SQL.identifier(queue_table), SQL.identifier(id_column), record_id))
    assert env.cr.fetchone()[0] == 1, 'Concurrent source write was not queued after publication'
    domain = [('id', '=', record_id)]
    assert not Source.search(domain + [('usl_search_text', 'ilike', 'Amber heliograph snapshot')])
    assert Source.search(domain + [('usl_search_text', 'ilike', 'Violet semaphore replacement')]).ids == [record_id]
    getattr(Source, worker_method)()
    assert Source.search(domain + [('usl_search_text', 'ilike', 'replacement Violet')]).ids == [record_id]
    print(json.dumps({'result': 'PASS', 'model': model_name, 'native_writer_blocked_on_queue_lock': True, 'new_invalidation_retained': True, 'old_fragment_excluded': True, 'current_pending_text_visible': True, 'next_batch_indexes_new_text': True}))
finally:
    resume.set()
    env.cr.rollback()
    env.invalidate_all()
    Source.browse(record_id).exists().unlink()
    project.exists().unlink()
    env.cr.commit()
