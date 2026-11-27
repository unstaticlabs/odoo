# Explain why a task is waiting

A collaborator who can edit a task can choose **Waiting** in its status menu,
including on personal tasks. Record an external cause, link one or more readable
records, or combine both. **Apply** saves the explanation; **Cancel** discards the
pending changes. The form and Kanban card show the unresolved causes.

Use **Review causes** to lift a cause. Linked tasks use Odoo's native dependencies:
completing or cancelling the last blocking task lets the task resume. Other
records have no universal completion state, so their causes are lifted explicitly.
Lifting a cause never changes the linked record. Native dependency-cycle checks
and the existing Later/Inbox reactivation behavior still apply.

New Waiting transitions require an unresolved cause, through the UI, ordinary ORM
and MCP alike. Existing unexplained Waiting tasks are not backfilled or rewritten.
Unrelated edits remain possible; their status menu can return them to In Progress.
Completing a task does not make it reopen when a cause later resolves.

## Access and privacy

Task write access governs changing its causes. Adding or restoring a reference
also requires current native read access to the referenced record. A reference
never shares a record or expands a person's company or delegated Agent access.
Readonly collaborators can inspect only the causes they may read.

After access is withdrawn, the next Task/reason read, export or context projection
omits the inaccessible reference's model, ID, name and link. A generic unavailable
message and an opaque task-owned cause handle let the task writer lift the cause
without rediscovering the private record. Opening a link checks access again.
Fetched record names are not stored in cause rows.

Task/reason authority is checked on the primary database. An outer replica-eligible
native request that encounters this boundary is retried by Odoo on primary. This
protects the Waiting journey; it does not change replica routing for other models
or recall information a client has already received. An already-running database
transaction retains its native snapshot semantics.

People and managed Agents use the same native actions, roles, company scope and
owner ceilings. References also retain the existing Agent hidden-model API decision; they do not create an alternative secret-model route. Serialized context does not mint the private recursion carrier.
Two concurrent cause resolutions rely on native Task writes and Odoo's whole-
transaction serialization retry; no additional lock service or local retry is used.

## MCP compatibility

The existing task-context tool probes the available field contract. On a server
with `usl_waiting_details`, one Task read returns scalar fields and safe causes
from the same snapshot. It reports explained causes, current permitted task
blockers, incomplete visibility and legacy unexplained Waiting distinctly.
Native dependency counters keep their existing meaning. Older servers retain the
previous dependency-only explanation, without requesting an unavailable field.
An advertised but invalid/denied new contract fails visibly rather than silently
falling back to a misleading explanation.

The existing public-method tool can call `action_set_waiting` and
`action_lift_waiting_cause`. Each action is one native transaction. Generic Task
writes retain the same explanation requirement; there is no Agent-only bypass.

## Qualification boundaries

Synthetic model tests exercise source/target roles, companies, revocation,
ordinary ORM/JSON/export/grouping, deleted references, legacy tasks, duplicates,
atomic failure, native task completion and managed Agent owner ceilings. Component
tests cover cancel, a single save with pending form edits, readonly relation
fields, unavailable references, personal tasks, historical resume and Kanban.
Real native desktop/mobile journeys, managed-Agent JSON-2, MCP SDK, simultaneous
native transactions and a paused physical replica complement these tests.

Cause rows are new ancillary Task data. Upgrades do not invent explanations or
rewrite existing Task, financial, attachment, message, Project or membership rows.
