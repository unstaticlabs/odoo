"""Exercise native edit/delete against a task-message refresh on disposable QA.

Run with Odoo shell and usl_home installed. The script commits only invented
fixtures, uses the ordinary author's native edit and an authorized human's native deletion and Odoo's transaction
retry boundary, removes its conversation fixtures and archives its identities to preserve native audit references. Never run against business data.
"""

import json
import threading
import time
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from markupsafe import Markup
from odoo import Command, SUPERUSER_ID, api
from odoo.http.retrying import retrying
from odoo.tests import new_test_user
from odoo.tools import SQL

if not env.cr.dbname.startswith("odoo_search_3758"):
    raise RuntimeError("Use a disposable odoo_search_3758 QA database only")

registry = env.registry
fixture_suffix = uuid4().hex[:12]
actor = new_test_user(env, login=f"qa-chatter-transaction-race-{fixture_suffix}", groups="project.group_project_user",
                      context={"no_reset_password": True, "mail_notify_force_send": False})
operator_groups = ["base.group_system", "project.group_project_manager"]
for xmlid in ("usl_access_control.group_distribution_administrator", "usl_access_control.group_irreversible_actions"):
    if env.ref(xmlid, raise_if_not_found=False):
        operator_groups.append(xmlid)
operator = new_test_user(env, login=f"qa-chatter-delete-operator-{fixture_suffix}", groups=",".join(operator_groups),
                         context={"no_reset_password": True, "mail_notify_force_send": False})
project = env["project.project"].create({"name": "[QA] Conversation race workspace"})
task = env["project.task"].with_context(tracking_disable=True).create({
    "name": "[QA] Conversation race task", "project_id": project.id,
    "user_ids": [Command.link(actor.id)],
})
actor_id, operator_id, task_id = actor.id, operator.id, task.id
messages = env["mail.message"]
reports = []

try:
    for operation in ("edit", "delete"):
        message = task.with_user(actor).message_post(
            body=Markup("<p>Amber heliograph earlier.</p>"),
            message_type="comment", subtype_xmlid="mail.mt_comment",
        )
        message_id = message.id
        messages |= env["mail.message"].browse(message_id)
        env["mail.message"]._cron_usl_index_task_messages()
        env.cr.commit()
        message.write({"body": "<p>Amber heliograph snapshot.</p>"})
        env.cr.commit()
        ready, writing, resume = threading.Event(), threading.Event(), threading.Event()
        writer_pid = []
        attempts = {"worker": 0, "writer": 0}

        def worker():
            with registry.cursor() as cr:
                model = api.Environment(cr, SUPERUSER_ID, {})["mail.message"]
                execute = cr.execute
                def observe(statement, *args, **kwargs):
                    value = execute(statement, *args, **kwargs)
                    code = " ".join((statement._sql_tuple[0] if isinstance(statement, SQL) else statement).split())
                    if code.startswith("SELECT id, subject, body FROM mail_message"):
                        ready.set()
                        assert resume.wait(20), "Refresh release timed out"
                    return value
                def publish():
                    attempts["worker"] += 1
                    model._cron_usl_index_task_messages()
                with patch.object(cr, "execute", observe):
                    retrying(publish, model.env)

        def writer():
            with registry.cursor() as cr:
                model = api.Environment(cr, actor_id if operation == "edit" else operator_id, {})["mail.message"]
                cr.execute("SELECT pg_backend_pid()")
                writer_pid.append(cr.fetchone()[0])
                execute = cr.execute
                def observe(statement, *args, **kwargs):
                    code = " ".join((statement._sql_tuple[0] if isinstance(statement, SQL) else statement).replace('"', '').split())
                    if "INSERT INTO usl_task_message_search_queue" in code or code.startswith("DELETE FROM mail_message"):
                        writing.set()
                    return execute(statement, *args, **kwargs)
                def mutate():
                    attempts["writer"] += 1
                    target = model.browse(message_id)
                    if operation == "edit":
                        target.write({"body": "<p>Violet semaphore replacement.</p>"})
                    else:
                        target.unlink()
                with patch.object(cr, "execute", observe):
                    retrying(mutate, model.env)

        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(worker)
            if not ready.wait(20):
                first.result(timeout=1)
                raise AssertionError("Worker did not reach its actual source snapshot")
            second = pool.submit(writer)
            try:
                assert writing.wait(20), "Author mutation did not reach invalidation/deletion"
                blocked = False
                for _ in range(50):
                    env.cr.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid=%s", (writer_pid[0],))
                    row = env.cr.fetchone()
                    if row and row[0] == "Lock":
                        blocked = True
                        break
                    time.sleep(.02)
                assert blocked and not second.done(), "Author bypassed the publication lock"
            finally:
                resume.set()
            first.result(timeout=30)
            second.result(timeout=30)
        env.cr.rollback()
        env.invalidate_all()
        Task = env["project.task"].with_user(actor)
        domain = [("id", "=", task_id)]
        assert not Task.search(domain + [("usl_search_text", "ilike", "Amber heliograph snapshot")])
        env.cr.execute("SELECT message_id FROM usl_task_message_search_queue WHERE message_id=%s", (message_id,))
        queued = env.cr.fetchone()
        if operation == "edit":
            assert queued == (message_id,), "Concurrent edit lost its next refresh"
            assert Task.search(domain + [("usl_search_text", "ilike", "Violet semaphore replacement")]).ids == [task_id]
            env["mail.message"]._cron_usl_index_task_messages()
            assert Task.search(domain + [("usl_search_text", "ilike", "replacement Violet")]).ids == [task_id]
            messages |= env["mail.message"].browse(message_id)
        else:
            assert queued is None
            env.cr.execute("SELECT count(*) FROM usl_task_message_search_fragment WHERE message_id=%s", (message_id,))
            assert env.cr.fetchone()[0] == 0, "Deleted source retained fragments"
            assert not env["mail.message"].browse(message_id).exists()
        reports.append({"operation": operation, "authorized_actor_blocked": True, "actor_role": "ordinary author" if operation == "edit" else "authorized human deletion", "native_attempts": attempts,
                        "old_text_absent": True, "queue_or_cascade_preserved": True})
        env.cr.commit()
    print(json.dumps({"result": "PASS", "scenarios": reports}))
finally:
    if "resume" in globals():
        resume.set()
    env.cr.rollback()
    env.invalidate_all()
    messages.exists().unlink()
    task.exists().unlink()
    project.exists().unlink()
    (actor | operator).exists().write({"active": False})
    env.cr.commit()
