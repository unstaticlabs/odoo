# Distribution Access Risk Inventory

This inventory is the developer contract for `usl_access_control`. Revisit it
when a product module introduces deletion, a lock, an authorization path, code
execution or an external-system lifecycle action.

| Risk surface | Protected operation | Enforcement | UX and evidence |
| --- | --- | --- | --- |
| Users and authorization | user create/delete; active, login, company, group, Pocket ID and identity-scope changes; group/privilege composition | `res.users`, `res.groups`, and generic protected-model guards | Users menu remains System-only; effective summary shown; successful human action audited; refusal logged |
| Agent capability composition | direct or implied AI Agent plus Irreversible Actions | user constraint plus whole-registry validation after group composition | clear incompatibility error; applies to RPC and `sudo()` because `sudo()` retains the actor UID |
| Company boundary | company creation and user allowed-company changes | `res.company.create` and `res.users.write` | company rules remain authoritative; action/refusal evidence as above |
| Accounting governance | fiscal-year, tax, sales, purchase and hard-lock dates | `res.company.write` | direct permission error; successful change audited |
| Business history | unlink of Accounting moves/payments, partners, products, sales, purchase, stock, project/task, B2C roots, platform sessions/payouts, TESE payslips, messages, tracking and attachments | generic `base.unlink` model inventory | ordinary archive/trash/state reversal remains available; permanent delete refused or audited |
| Documents/Paperless | permanent-deletion approval and remote permanent deletion | `usl.document` action overrides plus workspace capability flag | permanent-delete controls hidden without capability; local tombstone/history retained by Documents workflow |
| B2C governed sessions | unlock after approval/lock | `b2c.accounting.session.action_unlock` | Unlock button restricted to Irreversible Actions |
| Modules and metadata | module install/upgrade/uninstall; ACL, rule, model, field, view, menu, group, defaults, config parameters, OIDC binding and automation mutation | generic create/write/unlink guard plus module button overrides | safe technical read ACLs for Technical Administrator; mutation fails in backend |
| Code and jobs | arbitrary server-action execution and manual cron trigger | `ir.actions.server.run`, `ir.cron.method_direct_trigger` | protected permission required; scheduled internal work still runs under its configured attributable/system user |
| Electronic invoicing | Peppol/PDP registration, deregistration, branch/database connect/disconnect, reregistration and provider configuration sync | registration/configuration wizard and settings action overrides | high-risk controls hidden; live calls remain additionally gated by environment policy |
| Distribution audit | audit create only through internal recorder; no write or unlink through ORM | `usl.audit.event` | read-only list/form; immutable even for product administrators |

## Agent audit path

The abstract `base` extension records every Agent create, write and unlink on a
persistent model except infrastructure-noise models and the audit model itself.
It captures actor, Agent marker, time, model, record IDs/count, operation,
submitted values, selected before-values, origin and correlation ID. Secret,
credential, password, private-key and token-like values are redacted; binary
fields are represented by a marker. The event is committed in the same
transaction as the mutation.

Transient wizard records are excluded because they are transport state, not the
business result. Their protected methods call the central irreversible helper.
Infrastructure noise (`bus.bus`, outgoing mail and notifications, `ir.logging`)
is excluded to keep evidence operationally reviewable.

## Required extension review

For a new or changed action, compare at least these alternatives:

1. a standard Odoo/OCA recoverable workflow or archive/state transition;
2. application ACL/record-rule restriction;
3. a central irreversible guard when the action permanently destroys history,
   changes authority, moves a lock, executes arbitrary code or changes an
   external registration.

Prefer the first recoverable option. Keep ordinary app access in the owning
module. Add only the cross-product destructive boundary here, and add a backend
test covering Valentin, Roger, Prosper and an Agent. Add a company-isolation
case when the model is company-scoped, a visible capability check for a risky
button, and French translations for every new user-facing term.

Denied-attempt database events are intentionally not attempted: they would be
rolled back with the refused transaction. The structured warning is the durable
log integration point. If centralized logging is unavailable, that is an
operational readiness failure, not a reason to weaken the transaction boundary.
