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

Read-only enforcement applies per application at both the ORM and JSON-2
boundaries, including code that retains the Agent actor through `sudo()`.
Create, write, delete, archive, workflow, import, module, server-action and
unknown public methods are denied outside explicitly write-enabled scopes.
Agent keys work only with JSON-2 and the qualified API-document
endpoints; legacy RPC transports are rejected. The exact read allowlist is
generated from the audited Agent-eligible subset of `read_only` action-risk
entries and fails closed if code or policy identity drifts. A general low-risk
classification is not sufficient: only standard data-query methods and
individually named product helpers enter the Agent runtime allowlist. Specialized
helpers that bypass ordinary record rules remain unavailable; Agents use the
equivalent owner-scoped generic reads instead.

The only write exceptions are guarded collaboration methods on records the
Agent can already read: Chatter notes and comments, activities, self-following,
normal Chatter notifications, and short-lived Documents download grants.
Direct writes to messages, activities, followers, mail queues or grants remain
blocked.

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
