# Distribution Access Control Runbook

## Safety boundary

Keep these guards disabled in development, test, staging, reconstruction and
copied databases:

```text
USL_EINVOICE_LIVE_ENABLED=0
USL_EREPORTING_LIVE_ENABLED=0
```

Never test Peppol or French Approved Platform protection against a live
provider. Use the synthetic offline fixtures and mocked provider calls.

## Canonical named-user policy

The finalization policy in `scripts/pocket_id_dev.py` reconciles existing Odoo
users by exact login and email. It does not replace an existing identity with a
new record. Immutable Pocket ID subjects remain attached to the same users.

| Login | Profile | Company scope |
| --- | --- | --- |
| `admin` | sealed local `break_glass` / Full Product Administrator | all approved companies |
| `valentin` | Full Product Administrator | all approved companies |
| `roger@unstaticlabs.com` | Technical Administrator | all approved companies |
| `prosper` | Accounting Reviewer | Unstatic Labs only |

The historical `roger@xaic.cat` record remains inactive/historical and is not
reused as the active Roger identity. It is marked optional: migrated databases
classify the existing record, while clean installations do not manufacture a
historical user.

Apply the normal target-finalization workflow documented in the Pocket ID SSO
runbook. Run it twice during rehearsal. The second run must report the same
Odoo user IDs, provider subjects, company scopes and effective role groups; it
must not create another user or identity link.

After reconciliation, verify on each user form under **Access Rights**:

- the Distribution access summary names the intended role;
- only Valentin and the sealed administrator show Irreversible Actions;
- no AI Agent shows Irreversible Actions;
- Roger shows Accounting read-only and can reach B2C, Projects and Documents;
- Prosper reaches Accounting and its evidence, but not unrelated applications;
- allowed companies match the table above.

## Grant and change procedure

1. Decide the application access and allowed companies separately.
2. Mark autonomous identities with **AI Agent**.
3. Grant Irreversible Actions only to a named human with an approved need.
4. Never grant Irreversible Actions to an Agent. The backend rejects both a
   direct grant and a group that implies both capabilities.
5. Re-run the named-user policy after an approved profile change and capture
   the before/after user IDs, companies and effective groups.

Roger's Technical Administrator role deliberately uses Odoo's technical
inspection surfaces while backend guards refuse security mutation, module
maintenance and arbitrary server actions. Do not work around a refusal with a
temporary native manager group. Escalate the individual protected action to
Valentin or the sealed administrator instead.

Prosper may create or adjust annual-review accounting records, post, reset and
reconcile in unlocked periods. A lock change or permanent deletion must be
performed by an authorized human after separate review. Do not widen Prosper's
company list merely to make a cross-company report convenient.

## Audit review

Open **Settings > Distribution Audit**. Review by actor, time, model, operation,
origin and correlation ID. Agent events include submitted values and selected
before-values; secrets and binary payloads are redacted. Protected human
actions are recorded before their business operation in the same transaction,
so only successful committed work remains in the database.

Denied protected calls are emitted as `USL_PROTECTED_ACTION_DENIED` structured
warnings. They include the actor, Agent flag, model, record IDs, origin and
correlation ID. Review centralized Odoo logs for denied-attempt evidence: a
database row created inside the refused transaction would roll back with it.

Distribution audit rows cannot be changed or deleted through the ORM, including
by Full Product Administrators. Database and backup administrators remain an
external trust boundary. Protect database logs and backups with the production
retention and access policy.

## Break-glass use

The local `admin` identity is the single sealed emergency administrator. Keep
its password outside the application, rotate it through the existing Pocket ID
finalization procedure, and use it only when Pocket ID or the normal named
administrator is unavailable. Record the incident, reason, exact protected
actions and resulting Distribution Audit correlation IDs. Do not convert a
normal user to local-password login during an outage.

## Recovery after a refusal

- For a normal user, use the recoverable workflow or ask Valentin to perform
  the exact protected action.
- For an Agent, do not add a destructive group. Hand the action and context to
  an authorized human.
- For an unexpected refusal in an internal Odoo workflow, capture the model,
  operation and correlation ID, reproduce with offline fixtures, and amend the
  smallest enforcement point. Never bypass the central helper broadly.
- If the refusal concerns electronic invoicing, leave both live guards at zero
  until the approved production activation runbook explicitly says otherwise.
