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

The Access tab provides two owner-limited bulk profiles while creating or
editing an Agent. **Apply read-only profile** replaces the current delegation
with qualified native reader roles and confirms how many roles were granted.
Applications without a genuine reader role remain set to **No** because their
standard User role permits changes. Normal Chatter collaboration remains
available on records the Agent can read. **Apply read/write profile** selects
the highest safe application level the owner can delegate, including Settings
when available. Neither profile changes company scope or grants identity
administration or Irreversible Actions.

The owner and Agent record rules are both applied to every business operation
with the same active-company context. Losing an owner permission or company
removes it from the Agent. Restoring the owner's access does not silently
restore the Agent's delegation. Deactivating an owner suspends all of their
Agents; an owner or administrator must reactivate them explicitly.

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
accountable owner, credential expiry and effective companies.

The authenticated method `usl.agent.current_identity` is the compatibility
contract for clients that must confirm this identity before doing work.
