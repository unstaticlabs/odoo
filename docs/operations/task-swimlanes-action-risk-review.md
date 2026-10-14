# Task swimlane action review

Task swimlanes reuse native task permissions and task writes. They do not add
an administrator gate, grant new access or change existing action classifications.
Runtime discovery against the canonical 164-module registry adds four entries:
the task move RPC, its native write sink, its client transport and the fixed
read-only task property schema getter.

The RPC and write sink are **operational**: moving a task changes at most two
reviewed grouping fields and can produce native tracking, assignment and audit
effects. A later task edit can correct a move; this is not a promise of restoring
history bit for bit. The client is **transport** with one exact target,
`project.task.action_move_kanban_cell`.

## Authority and effects

The caller must have native read and write access to the task. The operation
locks that task, verifies the displayed source memberships and checks the
observed write date when supplied. It validates both destinations before one
native write. Native company rules, field access, task constraints and audit
remain authoritative. Related destinations must be caller-readable; supplied
labels or domains cannot grant access.

A many-to-many transfer removes the displayed source membership and adds the
destination while preserving other memberships. Moving a task with remaining
memberships into Unassigned opens a fixed native form for that exact readable
task; it does not clear those memberships. Changed date buckets, properties and
read-only dimensions recover through the task form without guessing values.
Keeping such a dimension unchanged still permits a move on a writable axis.

Related property labels use only native returned IDs and caller-scoped reads,
including readable archived references. An inaccessible label becomes
Unavailable; there is no privileged business-label fallback. Property
predicates, task population and bounded native group pages remain native.

## Automated evidence

`TestTaskSwimlane` covers atomic two-field outcomes, rejected second values,
stale memberships, native write denial, companies, preserved additional
memberships, personal stages, dates, properties and relation-label authority.
The canonical registry also executes the existing JSON2 Agent policy check and
approved operation context: a delegated write Agent completes the move with its
own write attribution and correlated audit events, while a read-only Agent is
denied before mutation. Native internal sudo side effects retain the existing
Agent operation scope; this feature introduces no scope factory or bypass.

The native browser suite checks bounded loading, exact destination focus,
readonly controls, unavailable grouping recovery and empty-result behavior.
Literal reversions and real synthetic browser journeys are recorded separately
in the PR qualification evidence; screenshots illustrate states rather than
prove those behaviors.


The view's property restore reads only the requested schema from native
Properties metadata on caller-readable task representatives in the selected
companies. It does not enumerate elevated project owners or return parent
names, property values or defaults. Revoking access to the last relevant task
makes that schema unavailable. This fixed getter is read-only; its exact Agent
allowlist entry still requires existing application delegation. The global
native definition getter and its existing Agent restrictions remain unchanged.
