# Distribution Access Control

The Distribution separates access to applications from permission to perform
irreversible actions. A user can operate several applications without gaining
permission to erase business history, move accounting locks, change
authorizations or execute destructive technical maintenance.

## Product roles

| Role or capability | Intended use | Effective product access | Explicit exclusions |
| --- | --- | --- | --- |
| Full Product Administrator | Attributable administration by Valentin and the sealed local administrator | All delivered applications, Accounting management and technical administration | None; protected actions are allowed and audited |
| Technical Administrator | Roger's daily product and safe technical work | B2C operations and sensitive evidence, Projects, Documents, Accounting read-only, audit and technical inspection | Accounting mutation, identity/security mutation, locks, permanent deletion, modules and arbitrary server actions |
| Accounting Reviewer | Prosper's annual Accounting work | Read and reversible write work in unlocked Accounting periods, posting, reset to draft, reconciliation, Accounting evidence and reports | Unrelated applications, user/security administration, locks and permanent deletion |
| AI Agent | Explicit machine-identity marker, combined with separate application groups | The application groups assigned to the Agent | Every irreversible action, even through an implied group, RPC or `sudo()` |
| Irreversible Actions | Separately visible human capability | Permanent deletion, lock and authorization changes, destructive technical maintenance and external registration changes | Incompatible with AI Agent |

Roles do not replace company access. Odoo's allowed-company list and active
company context remain authoritative for every role. Roger is reconciled to all
approved product companies; Prosper remains restricted to Unstatic Labs.

Application groups remain composable for Agents. For example, an Agent may
receive Accounting User and Project Manager and can then create, update, post,
reset and reconcile within those applications. The server still refuses the
irreversible boundary independently of application ACLs.

## Recoverability rule

A normal operational action is allowed when the result can be inspected and
reversed through the ordinary product workflow. Examples include editing a
draft, posting then resetting an unlocked accounting entry, reconciling then
unreconciling, changing a task, or moving a document to trash.

The separately protected boundary includes:

- permanent deletion of governed business roots and evidence;
- company creation, company-scope grants, user and group authorization;
- accounting, purchase, sales and hard-lock dates;
- module lifecycle, scheduled-action manual execution, arbitrary server
  actions and protected technical metadata;
- B2C accounting-session unlock;
- Paperless permanent deletion approval and execution;
- Peppol and French Approved Platform registration, deregistration and
  connection changes;
- mutation or deletion of Distribution audit history.

The backend is authoritative. High-risk buttons are also hidden when the user
lacks the capability, but hiding a control is never treated as enforcement.
Native Accounting views are rendered without create, edit, delete or workflow
controls for a read-only technical operator, while reports and ledger records
remain discoverable.
Denied calls return a direct explanation: the caller either needs the
Irreversible Actions capability, or an AI Agent must use a recoverable workflow
and ask an authorized human.

## Architecture decision

The implementation first retains standard Odoo groups, ACLs and company record
rules. They remain the best-supported way to express application access and
ordinary read/write scope. Native groups alone were not sufficient because
ACLs are additive, several manager groups bundle unrelated accounting or
technical powers, and an implied group cannot express an explicit Agent deny.

OCA role-template and audit-log add-ons were also considered. A role-template
add-on could improve assignment ergonomics, but it would still compose the same
additive groups and would not protect method calls or an accidental implied
Irreversible Actions capability. A general audit-log add-on would capture more
CRUD volume but would not define the recoverability boundary or reliably retain
a denied attempt whose business transaction rolls back. Neither option is in
the pinned product perimeter.

`usl_access_control` is therefore a thin product integration layer. It composes
native application groups into named roles, enforces the small irreversible
boundary in backend methods, rejects unsafe Agent group composition, records
successful protected actions, and records every Agent create/write/delete with
actor, model, record IDs, submitted changes, origin and correlation ID.
Structured server warnings retain denied-attempt evidence outside the rolled
back business transaction.

This is ongoing product behavior, not migration machinery. The add-on contains
no source bindings, importer, reconstruction model or parity evidence.
