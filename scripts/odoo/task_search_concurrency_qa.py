"""Prove native task-write/index publication concurrency on disposable QA data.

Run through Odoo shell with this checkout's usl_project installed. The script
commits its own synthetic fixtures, uses Odoo's normal transaction retry boundary,
and removes those fixtures afterwards. It never targets a production database.
"""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
from odoo import api, SUPERUSER_ID
from odoo.http.retrying import retrying

if not env.cr.dbname.startswith("odoo_search_3758"):
    raise RuntimeError("Use a disposable odoo_search_3758 QA database only")

registry = env.registry
Task = env['project.task']
project = env['project.project'].create({'name': '[QA] Search concurrency'})
task = Task.with_context(tracking_disable=True).create({'name': '[QA] Queue race', 'project_id': project.id, 'description': 'Amber heliograph earlier'})
task_id = task.id
Task._cron_usl_index_task_content()
env.cr.commit()
task.with_context(tracking_disable=True).write({'description': 'Amber heliograph snapshot'})
env.cr.commit()
ready, writer_ready, resume = threading.Event(), threading.Event(), threading.Event()
writer_pid = []

def worker():
    with registry.cursor() as cr:
        model = api.Environment(cr, SUPERUSER_ID, {})['project.task']
        execute = cr.execute
        def observe(query, *args, **kwargs):
            result = execute(query, *args, **kwargs)
            if isinstance(query, str) and query.startswith('SELECT id, name, description FROM project_task'):
                ready.set()
                assert resume.wait(20), 'Index worker release timed out'
            return result
        with patch.object(cr, 'execute', observe):
            model._cron_usl_index_task_content()
        cr.commit()

def writer():
    with registry.cursor() as cr:
        model = api.Environment(cr, SUPERUSER_ID, {})['project.task']
        cr.execute('SELECT pg_backend_pid()')
        writer_pid.append(cr.fetchone()[0])
        execute = cr.execute
        def observe(query, *args, **kwargs):
            if isinstance(query, str) and 'INSERT INTO usl_task_search_queue' in query:
                writer_ready.set()
            return execute(query, *args, **kwargs)
        with patch.object(cr, 'execute', observe):
            retrying(lambda: model.browse(task_id).with_context(tracking_disable=True).write({'description': 'Violet semaphore replacement'}), model.env)

try:
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(worker)
        assert ready.wait(20), 'Worker never reached the actual native source snapshot'
        second = pool.submit(writer)
        try:
            assert writer_ready.wait(20), 'Native task write never reached invalidation'
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
    env.cr.execute('SELECT count(*) FROM usl_task_search_queue WHERE task_id = %s', (task_id,))
    assert env.cr.fetchone()[0] == 1, 'Concurrent task write was not queued after publication'
    domain = [('id', '=', task_id)]
    assert not Task.search(domain + [('usl_search_text', 'ilike', 'Amber heliograph snapshot')])
    assert Task.search(domain + [('usl_search_text', 'ilike', 'Violet semaphore replacement')]).ids == [task_id]
    Task._cron_usl_index_task_content()
    assert Task.search(domain + [('usl_search_text', 'ilike', 'replacement Violet')]).ids == [task_id]
    print(json.dumps({'result': 'PASS', 'native_writer_blocked_on_queue_lock': True, 'new_invalidation_retained': True, 'old_fragment_excluded': True, 'current_pending_text_visible': True, 'next_batch_indexes_new_text': True}))
finally:
    resume.set()
    env.cr.rollback()
    env.invalidate_all()
    Task.browse(task_id).exists().unlink()
    project.exists().unlink()
    env.cr.commit()
