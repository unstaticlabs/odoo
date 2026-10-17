# Autonomous Agents

Agents are non-human Odoo identities owned by one accountable human. They use
Odoo through API credentials and remain visible as the actor in audit history.
They are not shared human accounts and cannot sign in through the browser.

## Authority

An Agent can use only the intersection of:

- the owner's current authority;
- the companies and application access delegated on the Agent;
- the Distribution safety policy.

Owners may delegate Settings when they have it. This lets an Agent configure
ordinary applications through the API. It never lets the Agent manage users,
authentication, access-control groups, ACLs, record rules, other Agents, API
credentials or its own lifecycle. Irreversible Actions are never delegable.

Each application can be set independently to **No access**, **Read-only**, or
one of the owner's native Odoo access levels. The two shortcuts prefill every
row with read-only or the highest safe owner level; they do not save or lock a
profile. Adjust individual rows, then use the normal Save or Discard controls.

New Agents start with owner-scoped read-only access. Existing read-only and
read/write assignments retain their meaning during upgrade. Settings is
delegable when available, but identity administration, credentials, secrets
and Irreversible Actions are never offered—even by the highest-access shortcut.
A newly installed application never expands an existing Agent silently.

Read access is the intersection of the Agent's delegated applications and
companies, the owner's current ACLs and record rules in the same company
context, and the platform safety policy. This includes private, assigned-only,
employee, Accounting and Documents rules. Losing an owner permission or
company removes it from the Agent immediately. Restoring the owner's access
does not silently restore the Agent's delegation. Deactivating an owner
suspends all of their Agents; an owner or administrator must reactivate them
explicitly.

Computed record-rule searches resolve as the current owner, including personal
employee and manager hierarchy rules. Rule compilation retains native Odoo's
sudo evaluation and caller search bounds; it does not elevate the returned
Agent records. Record filtering and writes use the owner's native rules for
the requested operation. Explicit caller search predicates keep their native
caller context.

An already-authorized Chatter notification renders its exact technical message
with native sudo while retaining the Agent's author identity and recipients.
This internal rendering returns HTML to the native notification workflow;
ordinary subsequent message reads still use the current owner's rule ceiling.
It does not extend the Agent's personal-message authority globally.

When owner authority contracts, Odoo removes the lost groups and companies
immediately and records an authority-reduction warning. The warning is
informational: the Agent may continue within its reduced authority while the
owner reviews and acknowledges the change. An acknowledgement clears the
warning but never restores access.

Read-only enforcement applies per application at both the ORM and JSON-2
boundaries, including code that retains the Agent actor through `sudo()`.
Create, write, delete, archive, workflow, import, module, server-action and
unknown public methods are denied outside explicitly write-enabled scopes.
Agent keys work only with JSON-2 and the qualified API-document
endpoints; legacy RPC transports are rejected. The exact read allowlist is
generated directly from reviewed `read_only` RPC classifications, without a
second method-name or explicit-helper selection. Existing collaboration methods
keep their shared collaboration contract. Making a reviewed method callable
does not change its native model, record, field, owner or company permissions;
unknown and protected operations retain their existing controls.

The only write exceptions are guarded collaboration methods on records the
Agent can already read: Chatter notes and comments, activities, self-following,
normal Chatter notifications, and short-lived Documents download grants.
Direct writes to messages, activities, followers, mail queues or grants remain
blocked.

One narrow repair belongs to that list. A Chatter body is stored as HTML, so an
Agent that sends markup without declaring it stores escaped tags that readers
see as characters, and the ordinary write path cannot correct them.
`mcp_revise_own_message` lets an identity rewrite the body of a comment or note
it posted itself, on a record it can still read, within a day of posting.
Odoo marks the message edited, the audit trail keeps the original body, and
everything else -- another author's message, a tracking message, a message on a
different record, an older one -- is refused. Rewriting history is not the
point; correcting your own fresh mistake is.

Some product steps are decisions rather than data entry: a person puts their
name to a judgement that will be read later by a reviewer or an auditor.
Continuing an expense without a receipt is one of them, and an Agent is refused
it by default. An owner who is willing to answer for what their Agent decides
can turn on **May record receipt decisions** on the Agent, under **Decisions
this Agent may record on your behalf**. The permission is granted per Agent,
requires write access to Expenses, is withdrawn automatically with that access,
and cannot be granted by the Agent itself -- Agents cannot see Agent records at
all.

The permission relaxes the refusal into a confirmation, never into a silent
write. The first call records nothing and returns the exact decision -- the
expense, employee, amount, category and written reason -- with an instruction
to put it in front of the employee or an Expense Manager. Only a second call
carrying `agent_confirmed=True` records it, and the Chatter note then names the
Agent, the owner whose authority it used, and the reason, so the decision reads
as attributable as a person's own.

Credential values and secret-bearing models or fields remain hidden even when
Settings is visible. Agents can inspect safe configuration identity, status,
expiry, digest and health metadata, but not passwords, private keys, API/OAuth
tokens, mail credentials, payment credentials, or integration secrets.

## Native Odoo experience

Every active internal human user can open **My Agents** from the user menu.
Administrators can also use **Settings > Users & Companies > Agents** and the
**Agents** smart button on a human user.

An Agent has a name, required purpose, owner, companies, default company and
normal Odoo application-access levels. The form shows its credentials and a
plain activity log. Suspending an Agent disables its backing identity and all
of its credentials without deleting attribution or history.

## Credentials

Agent credentials authenticate the Agent; permissions belong to the Agent,
not to individual keys. A key expires after 90 days, one year (the default),
five years or a custom period no longer than five years. Non-expiring keys are
not allowed.

Creating or revoking a key requires the owner's Pocket ID step-up. Odoo shows
the secret once and stores only the native hashed key plus non-secret metadata.
**Create replacement** issues a second key so an integration can switch before
the previous key is revoked.

## MCP identity

ChatGPT or another client authorizes the MCP connection as a human interaction,
but a governed Agent credential is the Odoo execution identity. Human API keys
are rejected. `odoo_describe_environment` reports the Agent, its purpose,
accountable owner, access mode, effective applications, credential expiry,
effective companies and any authority reduction.

The authenticated method `usl.agent.current_identity` is the compatibility
contract for clients that must confirm this identity before doing work.
The granular application-access response is schema version 3. A coordinated
release must keep that version aligned in Odoo, the MCP parser, and
`deploy/odoo-mcp/compatibility.json`.

Authenticated API documentation reports effective CRUD access and the public
methods Odoo permits for each model. Read-only applications therefore omit
ordinary mutations while retaining approved collaboration methods. This is a
catalogue aid for clients; Odoo still authenticates and authorizes every call,
including record- and argument-specific checks.

## Correcting and archiving work

A Project Agent with write access can correct one task activity through
`project.task.mcp_update_activity(activity_id, values)`. The supported fields
are `date_deadline`, `summary`, `note` and `user_id`. The activity keeps its id
and remains unfinished; the method cannot move it to another business record.
Use `activity_reschedule` when changing matching activity types together.

Writable Agents can use native project/task `action_archive` and
`action_unarchive`. Archiving hides records from active lists while retaining
their chatter, attachments and identity. Native child-task archive behavior
also applies. Read-only access does not permit either action.

Expense draft creation and reset retain native approval-activity cleanup within
the authorized expense workflow. This does not grant general activity deletion
or bypass native expense, company or Accounting checks.


Agents are collaborators acting with their delegated human access. Writable
Agents use the same native expense capabilities and business rules as humans
with equivalent permissions, including generic field writes and native
workflows. Existing delegation, read-only application access and irreversible
action controls still apply.

Expense correction adds precise context for decisions: the same transaction
returns the original and final financial values, with native tax totals and
explicit document and company currencies. This context helps an Agent explain
and check its work without adding Agent-specific restrictions.
