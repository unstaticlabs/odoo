"""Explained Waiting using native task dependencies and caller-owned causes."""
from collections import defaultdict

from psycopg2.errors import ReadOnlySqlTransaction

from odoo import _, api, fields, models
from odoo.exceptions import AccessError, ValidationError
from odoo.fields import Command, Domain
from odoo.tools import SQL
from odoo.addons.project.models.project_task import CLOSED_STATES


WAITING = "04_waiting_normal"
_SCOPE_KEY = "usl_task_waiting_mutation"
_TARGET_CHECK_KEY = "usl_task_waiting_target_check"
_TARGET_CHECK = object()


def _primary(method):
    # Native dataset/JSON2 routing scans inherited method metadata as well.
    method._readonly = False
    return method


def _require_primary(env):
    if env.cr.readonly:
        # The native HTTP router restarts the whole request on primary. This
        # also protects nested reads whose outer model is replica-eligible.
        raise ReadOnlySqlTransaction("Task blocker authority requires the primary database.")


class _WaitingMutation:
    def __init__(self, env, task_ids=(), *, creating=False):
        self.env = env
        self.uid = env.uid
        self.cr = env.cr
        self.task_ids = set(task_ids)
        self.creating = creating
        self.inside = False
        self.created_ids = []

    def matches(self, env):
        return self.uid == env.uid and self.cr is env.cr



class TaskWaitingReason(models.Model):
    _name = "project.task.waiting.reason"
    _description = "Task Waiting Cause"
    _order = "sequence, id"
    _rec_name = "reason"

    task_id = fields.Many2one("project.task", required=True, ondelete="cascade", index=True)
    kind = fields.Selection([("external", "External cause"), ("reference", "Blocking record")], string="Cause type", required=True)
    reason = fields.Text(string="External cause")
    target_model = fields.Char(string="Record type", readonly=True)
    target_id = fields.Many2oneReference(model_field="target_model", string="Blocking record", readonly=True)
    lifted = fields.Boolean(string="Lifted", default=False)
    sequence = fields.Integer(default=10)

    @api.model
    def _access_domain(self, operation):
        _require_primary(self.env)
        domain = super()._access_domain(operation)
        if domain.is_false() or self.env.su:
            return domain
        if operation == "read" and self.env.context.get(_TARGET_CHECK_KEY) is _TARGET_CHECK:
            # A target whose own authority depends on this technical cause
            # cannot bootstrap access to itself through that same cause.
            return Domain.FALSE
        parent_operation = "read" if operation == "read" else "write"
        domain &= Domain("task_id", "access", parent_operation)
        if operation != "read":
            return domain
        caller = self.sudo(False).with_context(active_test=False)

        def readable(record):
            # Structural data only, like the native many2one access operator.
            item = record.sudo()
            if item.kind == "external":
                return True
            if (item.target_model not in caller.env or item.target_model == self._name
                    or not caller.env["project.task"]._usl_waiting_reference_allowed(item.target_model)):
                return False
            target = caller.env[item.target_model].with_context(**{_TARGET_CHECK_KEY: _TARGET_CHECK}).browse(item.target_id).exists()
            return bool(target and target.has_access("read"))

        def optimize(_domain, model):
            # Only discriminator metadata for caller-readable Tasks, never
            # names/links or an independent list of allowed people.
            tasks = caller.env["project.task"]._search([])
            caller.env.cr.execute(SQL(
                "SELECT target_model, array_agg(DISTINCT target_id) FROM %s "
                "WHERE kind = 'reference' AND task_id IN %s GROUP BY target_model",
                SQL.identifier(self._table), tasks.subselect(),
            ))
            targets = caller.env.cr.fetchall()
            branches = [Domain("kind", "=", "external")]
            for name, identifiers in targets:
                if (name not in caller.env or name == self._name
                        or not caller.env["project.task"]._usl_waiting_reference_allowed(name)):
                    continue
                target = caller.env[name].with_context(**{_TARGET_CHECK_KEY: _TARGET_CHECK})
                if not target.has_access("read"):
                    continue
                branches.append(Domain("target_model", "=", name) & Domain(
                    "target_id", "any!", target._search([("id", "in", identifiers)]),
                ))
            return Domain.OR(branches)

        return domain & Domain.custom(optimize=optimize, predicate=readable)

    def _waiting_scope(self):
        scope = self.env.context.get(_SCOPE_KEY)
        return scope if isinstance(scope, _WaitingMutation) and scope.matches(self.env) and scope.inside else None

    @api.model
    def _validated_values(self, values):
        values = dict(values)
        kind = values.get("kind")
        if kind == "external":
            text = values.get("reason")
            if not isinstance(text, str) or not text.strip():
                raise ValidationError(_("Enter an external cause or choose a blocking record."))
            values.update(reason=text.strip(), target_model=False, target_id=False)
        elif kind == "reference":
            name, identifier = values.get("target_model"), values.get("target_id")
            if (not isinstance(name, str) or name not in self.env or name == self._name or self.env[name]._transient
                    or self.env[name]._abstract or not self.env[name]._auto
                    or isinstance(identifier, bool) or not isinstance(identifier, int) or identifier <= 0):
                raise ValidationError(_("Choose an available blocking record."))
            target = self.sudo(False).env[name].browse(identifier).exists()
            if (not self.env["project.task"]._usl_waiting_reference_allowed(name)
                    or not target or not target.has_access("read")):
                raise AccessError(_("The blocking record is unavailable."))
            # Never retain a fetched record label in reason text/history.
            values["reason"] = False
        else:
            raise ValidationError(_("Choose an external cause or a blocking record."))
        return values

    @api.model_create_multi
    def create(self, vals_list):
        self.check_access("create")
        values = [self._validated_values(vals) for vals in vals_list]
        if any(isinstance(v.get("task_id"), bool) or not isinstance(v.get("task_id"), int)
               or v["task_id"] <= 0 for v in values):
            raise ValidationError(_("Choose an available task."))
        tasks = self.env["project.task"].browse(sorted({v["task_id"] for v in values})).exists()
        tasks.check_access("write")
        if len(tasks) != len({v["task_id"] for v in values}):
            raise AccessError(_("The task is unavailable."))
        scope = self._waiting_scope()
        if scope:
            if not scope.creating and not set(tasks.ids) <= scope.task_ids:
                raise AccessError(_("This cause belongs to another task."))
            for task in tasks:
                dependency_ids = [v["target_id"] for v in values
                                  if v["task_id"] == task.id and v["kind"] == "reference"
                                  and v["target_model"] == "project.task" and not v.get("lifted")]
                if dependency_ids:
                    task.with_context(tracking_disable=True).write({
                        "depend_on_ids": [Command.link(identifier) for identifier in dependency_ids],
                    })
            records = super().create(values)
            scope.created_ids.extend(records.ids)
            return records
        scope = _WaitingMutation(self.env, tasks.ids)
        with self.env.cr.savepoint():
            for task in tasks:
                task.with_context(**{_SCOPE_KEY: scope}).write({
                    "usl_waiting_reason_ids": [Command.create({k: v for k, v in vals.items() if k != "task_id"})
                                               for vals in values if vals["task_id"] == task.id],
                })
        return self.browse(scope.created_ids)

    def _structural_rows(self, operation):
        self.check_access(operation)
        # Read only parent/reference keys for an already-authorized mutation.
        # A writer can lift a now-unreadable link without reading its target.
        return self.sudo().read(["task_id", "kind", "target_model", "target_id", "lifted", "reason"])

    def _unlink_dependency_if_last(self, task, target_id):
        # Only reference keys for the already-authorized owning task. Multiple
        # explicit causes may share one native many2many dependency; lifting
        # one must not remove the link still used by another active cause.
        self.flush_model(["task_id", "kind", "target_model", "target_id", "lifted"])
        self.env.cr.execute("""SELECT 1 FROM project_task_waiting_reason
            WHERE task_id = %s AND kind = 'reference' AND target_model = 'project.task'
              AND target_id = %s AND NOT lifted AND id NOT IN %s LIMIT 1""",
            [task.id, target_id, tuple(self.ids)])
        if not self.env.cr.fetchone():
            task.with_context(tracking_disable=True).write({"depend_on_ids": [Command.unlink(target_id)]})

    def write(self, vals):
        rows = self._structural_rows("write")
        for row in rows:
            for name in ("task_id", "kind", "target_model", "target_id"):
                old = row[name][0] if name == "task_id" else row[name]
                if name in vals and vals[name] != old:
                    raise ValidationError(_("Add a new cause instead of moving or changing an existing reference."))
            if row["kind"] == "external" and "reason" in vals:
                self._validated_values({**row, **vals})
            if row["kind"] == "reference" and row["lifted"] and vals.get("lifted") is False:
                self._validated_values(row)
        tasks = self.env["project.task"].browse(sorted({r["task_id"][0] for r in rows}))
        tasks.check_access("write")
        scope = self._waiting_scope()
        if scope:
            if not set(tasks.ids) <= scope.task_ids:
                raise AccessError(_("This cause belongs to another task."))
            if "lifted" in vals:
                for row in rows:
                    if row["kind"] == "reference" and row["target_model"] == "project.task":
                        task = self.env["project.task"].browse(row["task_id"][0])
                        if vals["lifted"]:
                            self._unlink_dependency_if_last(task, row["target_id"])
                        else:
                            task.with_context(tracking_disable=True).write({"depend_on_ids": [Command.link(row["target_id"])]})
            return super().write(vals)
        scope = _WaitingMutation(self.env, tasks.ids)
        with self.env.cr.savepoint():
            for task in tasks:
                task.with_context(**{_SCOPE_KEY: scope}).write({"usl_waiting_reason_ids": [
                    Command.update(row["id"], vals) for row in rows if row["task_id"][0] == task.id
                ]})
        return True

    def unlink(self):
        rows = self._structural_rows("unlink")
        tasks = self.env["project.task"].browse(sorted({r["task_id"][0] for r in rows}))
        tasks.check_access("write")
        scope = self._waiting_scope()
        if scope:
            if not set(tasks.ids) <= scope.task_ids:
                raise AccessError(_("This cause belongs to another task."))
            for row in rows:
                if row["kind"] == "reference" and row["target_model"] == "project.task":
                    self._unlink_dependency_if_last(self.env["project.task"].browse(row["task_id"][0]), row["target_id"])
            return super().unlink()
        scope = _WaitingMutation(self.env, tasks.ids)
        with self.env.cr.savepoint():
            for task in tasks:
                task.with_context(**{_SCOPE_KEY: scope}).write({"usl_waiting_reason_ids": [
                    Command.delete(row["id"]) for row in rows if row["task_id"][0] == task.id
                ]})
        return True


class TaskWaiting(models.Model):
    _inherit = "project.task"

    usl_waiting_reason_ids = fields.One2many("project.task.waiting.reason", "task_id", string="Waiting causes", copy=False)
    usl_waiting_details = fields.Json(string="Waiting details", compute="_compute_usl_waiting_details", compute_sudo=False)
    usl_waiting_is_blocked = fields.Boolean(string="Has unresolved waiting causes", compute="_compute_usl_waiting_is_blocked", compute_sudo=True)

    @property
    def TASK_PORTAL_READABLE_FIELDS(self):
        return super().TASK_PORTAL_READABLE_FIELDS | {
            "usl_waiting_reason_ids", "usl_waiting_details", "usl_waiting_is_blocked",
        }

    @property
    def TASK_PORTAL_WRITABLE_FIELDS(self):
        # Field integration does not grant Task write: native ACLs and sharing
        # rules still decide whether this caller can edit the parent Task.
        return super().TASK_PORTAL_WRITABLE_FIELDS | {"usl_waiting_reason_ids"}

    @api.depends_context("uid", "allowed_company_ids")
    @api.depends("state", "usl_waiting_reason_ids", "usl_waiting_reason_ids.reason",
                 "usl_waiting_reason_ids.lifted", "depend_on_ids.state")
    def _compute_usl_waiting_details(self):
        persisted = self.filtered(lambda task: isinstance(task.id, int))
        details = {}
        for offset in range(0, len(persisted), 100):
            response = persisted[offset:offset + 100].get_waiting_details()
            details.update((item["id"], item) for item in response["tasks"])
        for task in self:
            task.usl_waiting_details = details.get(task.id, False)

    @api.model
    def _access_domain(self, operation):
        if operation == "read":
            _require_primary(self.env)
        return super()._access_domain(operation)

    @_primary
    def read(self, fields=None, load="_classic_read"):
        return super().read(fields=fields, load=load)

    @api.model
    @_primary
    def search_read(self, domain=None, fields=None, offset=0, limit=None, order=None):
        return super().search_read(domain=domain, fields=fields, offset=offset, limit=limit, order=order)

    @_primary
    def web_read(self, specification):
        return super().web_read(specification)

    @api.model
    @_primary
    def web_search_read(self, domain, specification, offset=0, limit=None, order=None, count_limit=None):
        return super().web_search_read(domain, specification, offset=offset, limit=limit, order=order, count_limit=count_limit)

    def _usl_has_open_waiting_causes(self):
        self.ensure_one()
        if not self.id:
            for row in self.usl_waiting_reason_ids:
                if row.lifted:
                    continue
                if row.kind == "external" and row.reason and row.reason.strip():
                    return True
                if row.kind == "reference" and row.target_model in self.env and row.target_id:
                    target = self.env[row.target_model].sudo(False).browse(row.target_id)
                    if target.has_access("read") and (row.target_model != "project.task" or target.state not in CLOSED_STATES):
                        return True
            return bool(self.allow_task_dependencies and super().is_blocked_by_dependences())
        manual, blocking_ids = self._usl_waiting_facts()
        return manual or bool(blocking_ids)

    def _usl_waiting_facts(self):
        self.ensure_one()
        self.env["project.task.waiting.reason"].flush_model(["task_id", "kind", "target_model", "target_id", "lifted"])
        # This is the owning task's aggregate, like native dependency counters.
        # No target identity or name escapes before current target read access.
        self.env.cr.execute("""SELECT
            bool_or(NOT lifted AND (kind = 'external' OR target_model <> 'project.task')),
            array_agg(target_id) FILTER (WHERE NOT lifted AND kind = 'reference' AND target_model = 'project.task')
            FROM project_task_waiting_reason WHERE task_id = %s""", [self.id])
        manual, explicit_ids = self.env.cr.fetchone()
        explicit_ids = set(explicit_ids or [])
        dependencies = self.sudo().depend_on_ids
        if not self.allow_task_dependencies:
            dependencies = dependencies.filtered(lambda dependency: dependency.id in explicit_ids)
        # Same closure states as native project.task.is_blocked_by_dependences.
        blocking_ids = set(dependencies.filtered(lambda dependency: dependency.state not in CLOSED_STATES).ids)
        return bool(manual), blocking_ids

    @api.depends("usl_waiting_reason_ids", "usl_waiting_reason_ids.lifted", "usl_waiting_reason_ids.target_model",
                 "usl_waiting_reason_ids.target_id", "depend_on_ids.state", "stage_id")
    def _compute_state(self):
        super()._compute_state()
        for task in self:
            if task.state not in CLOSED_STATES and task._usl_has_open_waiting_causes():
                task.state = WAITING

    def is_blocked_by_dependences(self):
        _require_primary(self.env)
        if not self.exists() or not self.has_access("read"):
            raise AccessError(_("The task is unavailable."))
        return self._usl_has_open_waiting_causes()

    @api.depends("state", "depend_on_ids.state", "usl_waiting_reason_ids", "usl_waiting_reason_ids.lifted")
    def _compute_usl_waiting_is_blocked(self):
        for task in self:
            task.usl_waiting_is_blocked = bool(task.id and task._usl_has_open_waiting_causes())

    def _usl_enforce_waiting_causes(self):
        records = self.filtered(lambda task: task.state not in CLOSED_STATES and task.state != WAITING
                                and task._usl_has_open_waiting_causes())
        if records:
            self.env.add_to_compute(self._fields["state"], records)
            records._recompute_recordset(["state"])

    @api.model_create_multi
    def create(self, vals_list):
        self.check_access("create")
        for vals in vals_list:
            if vals.get("state") == WAITING and not (vals.get("depend_on_ids") or vals.get("usl_waiting_reason_ids")):
                raise ValidationError(_("Add an external cause or a blocking record before choosing Waiting."))
        scope = _WaitingMutation(self.env, creating=True)
        scoped = self.with_context(**{_SCOPE_KEY: scope})
        scope.inside = True
        try:
            records = super(TaskWaiting, scoped).create(vals_list)
        finally:
            scope.inside = False
        for task, vals in zip(records, vals_list):
            if vals.get("state") == WAITING and not task._usl_has_open_waiting_causes():
                raise ValidationError(_("Add an external cause or a blocking record before choosing Waiting."))
        return records.with_env(self.env)

    def write(self, vals):
        if not self:
            return super().write(vals)
        self.check_access("write")
        entering = self.filtered(lambda task: task.state != WAITING) if vals.get("state") == WAITING else self.browse()
        if entering and not (vals.get("usl_waiting_reason_ids") or vals.get("depend_on_ids")):
            if any(not task._usl_has_open_waiting_causes() for task in entering):
                raise ValidationError(_("Add an external cause or a blocking record before choosing Waiting."))
        if "usl_waiting_reason_ids" not in vals:
            with self.env.cr.savepoint():
                result = super().write(vals)
                if any(not task._usl_has_open_waiting_causes() for task in entering):
                    raise ValidationError(_("Add an external cause or a blocking record before choosing Waiting."))
                return result
        self.check_access("write")
        scope = self.env.context.get(_SCOPE_KEY)
        if not isinstance(scope, _WaitingMutation) or not scope.matches(self.env):
            scope = _WaitingMutation(self.env, self.ids)
        if not set(self.ids) <= scope.task_ids:
            raise AccessError(_("This cause belongs to another task."))
        scoped = self.with_context(**{_SCOPE_KEY: scope})
        previous = scope.inside
        scope.inside = True
        try:
            result = super(TaskWaiting, scoped).write(vals)
        finally:
            scope.inside = previous
        if any(not task._usl_has_open_waiting_causes() for task in entering):
            raise ValidationError(_("Add an external cause or a blocking record before choosing Waiting."))
        return result

    def action_set_waiting(self, reason=None, references=None):
        """Set Waiting once with external text and/or readable {model,id} records."""
        self.ensure_one()
        self.check_access("write")
        commands = []
        if reason is not None:
            commands.append(Command.create({"kind": "external", "reason": reason}))
        if references is not None and not isinstance(references, list):
            raise ValidationError(_("Choose available blocking records."))
        Reason = self.env["project.task.waiting.reason"]
        Reason.flush_model(["task_id", "kind", "target_model", "target_id", "lifted"])
        self.env.cr.execute("""SELECT target_model, target_id FROM project_task_waiting_reason
            WHERE task_id = %s AND kind = 'reference' AND NOT lifted""", [self.id])
        existing = set(self.env.cr.fetchall())
        for reference in references or []:
            if not isinstance(reference, dict) or set(reference) != {"model", "id"}:
                raise ValidationError(_("Choose available blocking records."))
            values = Reason._validated_values({"kind": "reference", "target_model": reference["model"], "target_id": reference["id"]})
            key = (values["target_model"], values["target_id"])
            if key not in existing:
                commands.append(Command.create(values))
                existing.add(key)
        if not commands and not self._usl_has_open_waiting_causes():
            raise ValidationError(_("Add an external cause or a blocking record before choosing Waiting."))
        with self.env.cr.savepoint():
            self.write({"state": WAITING, **({"usl_waiting_reason_ids": commands} if commands else {})})
        return {"id": self.id, "state": self.state}

    def action_lift_waiting_cause(self, cause_id):
        """Lift one owned cause; source record permissions and data are unchanged."""
        self.ensure_one()
        self.check_access("write")
        if isinstance(cause_id, bool) or not isinstance(cause_id, int):
            raise ValidationError(_("The waiting cause is unavailable."))
        # Structural identity only after current Task authority, never a target
        # model/id/name. A now-hidden reference remains removable by its owner.
        self.env.cr.execute("SELECT id FROM project_task_waiting_reason WHERE id = %s AND task_id = %s", [cause_id, self.id])
        if not self.env.cr.fetchone():
            raise ValidationError(_("The waiting cause is unavailable."))
        with self.env.cr.savepoint():
            self.write({"usl_waiting_reason_ids": [Command.update(cause_id, {"lifted": True})]})
        return {"id": self.id, "state": self.state}

    def get_waiting_details(self):
        """Current caller-safe causes; read-only business data on primary."""
        _require_primary(self.env)
        if not self or len(self) > 100 or len(self.exists()) != len(self) or not self.has_access("read"):
            raise AccessError(_("The task is unavailable."))
        Reason = self.env["project.task.waiting.reason"]
        Reason.flush_model(["task_id", "kind", "lifted"])
        # Only owned cause handles/status: never private target identities or
        # names. Unreadable reference rows stay absent from ordinary ORM reads.
        self.env.cr.execute("""SELECT id, task_id, kind, lifted FROM project_task_waiting_reason
            WHERE task_id IN %s ORDER BY sequence, id""", [tuple(self.ids)])
        structural = defaultdict(list)
        for identifier, task_id, kind, lifted in self.env.cr.fetchall():
            structural[task_id].append((identifier, kind, lifted))
        visible = {row.id: row for row in Reason.with_context(active_test=False).search([("task_id", "in", self.ids)])}
        results = []
        for task in self:
            manual, blocking_ids = task._usl_waiting_facts()
            native_ids = set(task.sudo().with_context(active_test=False).depend_on_ids.ids)
            native_readable = self.sudo(False).with_context(active_test=False).browse(sorted(native_ids))._filtered_access("read")
            native_by_id = {row.id: row for row in native_readable}
            causes, represented = [], set()
            for identifier, kind, lifted in structural[task.id]:
                row = visible.get(identifier)
                item = {"id": identifier, "kind": kind, "lifted": lifted}
                if not row:
                    causes.append({**item, "kind": "unavailable", "is_blocking": None,
                                   "message": _("The linked record is no longer accessible.")})
                    continue
                if kind == "external":
                    causes.append({**item, "reason": row.reason, "is_blocking": not lifted})
                    continue
                target = self.sudo(False).env[row.target_model].with_context(active_test=False).browse(row.target_id)
                # An authorized reference row does not replace this current
                # target check (including deletion between reads).
                if not target.exists() or not target.has_access("read"):
                    causes.append({**item, "kind": "unavailable", "is_blocking": None,
                                   "message": _("The linked record is no longer accessible.")})
                    continue
                is_task = row.target_model == "project.task"
                if is_task and not lifted:
                    represented.add(row.target_id)
                is_blocking = not lifted and (not is_task or target.id in blocking_ids)
                causes.append({**item, "kind": "task" if is_task else "reference", "is_blocking": is_blocking,
                               "reference": {"model": row.target_model, "id": target.id, "display_name": target.display_name,
                                             **({"state": target.state} if is_task else {})}})
            for identifier, dependency in native_by_id.items():
                if identifier not in represented:
                    causes.append({"id": False, "kind": "task", "lifted": False,
                                   "is_blocking": identifier in blocking_ids,
                                   "reference": {"model": "project.task", "id": identifier, "display_name": dependency.display_name, "state": dependency.state}})
            hidden_native = len(native_ids - set(native_by_id))
            if hidden_native and not any(item["kind"] == "unavailable" for item in causes):
                causes.append({"id": False, "kind": "unavailable", "is_blocking": None,
                               "message": _("A linked task is no longer accessible.")})
            blocked = manual or bool(blocking_ids)
            results.append({"id": task.id, "state": task.state, "has_open_causes": blocked,
                            "legacy_unexplained": task.state == WAITING and not blocked,
                            "can_change": task.has_access("write"), "causes": causes,
                            "native_dependencies": {"enabled": task.allow_task_dependencies,
                                "total_count": len(native_ids), "visible_count": len(native_by_id),
                                "complete": not hidden_native}})
        return {"schema": "usl-task-waiting/v1", "tasks": results}

    def action_open_waiting_reference(self, cause_id):
        self.ensure_one()
        _require_primary(self.env)
        if not self.has_access("read"):
            raise AccessError(_("The task is unavailable."))
        cause = self.env["project.task.waiting.reason"].search([
            ("id", "=", cause_id), ("task_id", "=", self.id), ("kind", "=", "reference"),
        ], limit=1)
        if not cause:
            raise AccessError(_("The blocking record is unavailable."))
        target = self.sudo(False).env[cause.target_model].browse(cause.target_id).exists()
        if not target or not target.has_access("read"):
            raise AccessError(_("The blocking record is unavailable."))
        action = target.get_formview_action()
        action["context"] = {key: value for key, value in action.get("context", {}).items()
                             if key not in {_SCOPE_KEY, _TARGET_CHECK_KEY}}
        return action

    @api.model
    def _usl_waiting_reference_allowed(self, name):
        # Respect the installed Distribution's existing Agent API read decision
        # as well as native record access. This optional integration adds no
        # second model list and does not affect ordinary human permissions.
        lookup = getattr(self, "_usl_managed_agent", None)
        agent = lookup() if lookup else False
        if agent:
            from odoo.addons.usl_access_control.controllers.json2 import AGENT_HIDDEN_API_MODELS
            return name not in AGENT_HIDDEN_API_MODELS
        return True

    @api.model
    def get_waiting_reference_models(self):
        _require_primary(self.env)
        self.check_access("read")
        # Registry labels are model metadata, not business-record contents.
        result = []
        for name in self.env.registry:
            model = self.sudo(False).env[name]
            if (model._abstract or model._transient or name == "project.task.waiting.reason"
                    or not model._auto or not self._usl_waiting_reference_allowed(name) or not model.has_access("read")):
                continue
            result.append({"model": name, "label": model._description})
        # Native translated model labels are metadata, not business contents.
        # Only models already admitted by the caller's own read ACL are listed.
        labels = {item["model"]: item["name"] for item in self.env["ir.model"].sudo().search_read(
            [("model", "in", [item["model"] for item in result])], ["model", "name"],
        )}
        for item in result:
            item["label"] = labels.get(item["model"], item["label"])
        return sorted(result, key=lambda item: (item["label"], item["model"]))

    @api.model
    def search_waiting_references(self, model, query="", limit=12):
        """Native autocomplete with fresh target authority, never a replica."""
        _require_primary(self.env)
        self.check_access("read")
        if (not isinstance(model, str) or model not in self.env or model == "project.task.waiting.reason"
                or self.env[model]._abstract or self.env[model]._transient or not self.env[model]._auto
                or not self._usl_waiting_reference_allowed(model)):
            raise ValidationError(_("Choose an available record type."))
        if not isinstance(query, str) or len(query) > 2048 or isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
            raise ValidationError(_("Enter a shorter record search."))
        Target = self.sudo(False).env[model]
        if not Target.has_access("read"):
            raise AccessError(_("The blocking record is unavailable."))
        matches = Target.name_search(query, limit=limit)
        readable = Target.browse([identifier for identifier, _label in matches])._filtered_access("read")
        return [{"model": model, "id": row.id, "display_name": row.display_name} for row in readable]
