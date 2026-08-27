# Distribution action-risk inventory

The action-risk inventory is the release contract for the exact delivered Odoo
registry. It complements Odoo ACLs and record rules; it does not add another
CRUD permission. Every externally reachable product action and every
consequential internal mutation sink must be discovered, reviewed, classified
and covered by automated evidence. There is no legacy baseline, wildcard
exception or “review later” state.

The two authoritative product artifacts ship in `usl_access_control`:

- `policy/action_surface.json` is generated from the reviewed source and exact
  runtime registry;
- `policy/action_policy.json` contains the explicit classification and evidence
  contract for each stable action key.

Their canonical combined SHA-256 is stored in release identity, embedded in the
Distribution image label
`com.unstaticlabs.odoo.action-risk-policy-sha256`, and verified against the
database and candidate before production admission.

## Classifications

Every discovered action key appears exactly once in the policy:

| Classification | Meaning | Required evidence |
| --- | --- | --- |
| `read_only` | Cannot mutate product or external state | static/runtime proof that no mutation or external sink is reachable |
| `recoverable` | A supported product workflow restores the prior governed state and preserves required history | perform, reverse and verify the documented reversal action |
| `protected` | Crosses the irreversible-action boundary | stable guard key plus ordinary-human, Agent, `sudo()` Agent, authorized-human and immutable-audit tests |
| `transport` | Only validates or forwards input to declared classified actions | exact target action keys and proof that no other sink is reachable |
| `system_internal` | Required framework behavior that an ordinary RPC/controller actor cannot invoke | explicit reachability proof and internal caller evidence |

Grouping is allowed only through explicit action-key lists. Module-wide rules,
prefix patterns and wildcards are forbidden because they could silently
classify a newly introduced action.

Authorization or company-scope changes, accounting lock movement, arbitrary
code or job execution, module lifecycle, permanent deletion of governed data,
and external registration or deletion default to `protected`. An external
effect may be `recoverable` only when its remote reversal and preservation of
local evidence are both tested. Uncertainty is resolved as `protected` until
stronger recovery evidence exists.

## Required review procedure

For every product, Odoo or pinned OCA revision that changes the discovered
surface:

1. Run `make action-risk-discover` and inspect the candidate diff. Discovery
   reads the running `odoo_dev` registry and never creates policy decisions.
2. Trace each added or changed entry point through delegates to its mutation,
   `sudo()`, raw SQL, filesystem, messaging and provider sinks. Check its ACLs,
   record rules, company behavior and externally reachable callers.
3. Compare the native Odoo/OCA recoverable workflow with ACL/record-rule
   restriction and a protected capability gate. Prefer a real recovery path;
   never label a destructive workflow recoverable merely because a button is
   named “Reset”.
4. Add one explicit classification, consequence, rationale, domain and
   automated evidence identifier. Add a reversal key for `recoverable`, a
   stable guard key for `protected`, exact targets for `transport`, or
   reachability proof for `system_internal`.
5. Add or reference behavioral evidence. Protected authorization must execute
   before any local or remote side effect.
6. Install the central irreversible guard at the smallest semantic boundary
   when the action is protected. Keep ordinary application access in its
   owning module.
7. Run `make action-risk-refresh`. Refresh accepts only reviewed policy; it
   must not invent, copy forward or auto-approve classifications. It seals the
   canonical digest and derives the compact protected runtime policy. After a
   policy-only edit that does not refresh the surface, run
   `make action-risk-compile-policy`.
8. Run `make action-risk-inventory`, the affected add-on tests, and
   `make action-risk-runtime` against `odoo_dev`, then compile the delivered
   bundles with `make product-assets`. A release also runs the check on a
   disposable clean installation and the reconstructed target registry.

Agents may divide review into bounded module or functional-domain batches. The
coordinating agent owns the merged snapshot, resolves overlaps, and must finish
with zero unclassified, ambiguous or stale entries. A named human attestation
and reviewer date are not required; the rationale and passing evidence are the
review record.

## Discovery and release gates

`scripts/action_risk_inventory.py` provides `discover`, `refresh`,
`compile-runtime-policy`, and `check-source`. The checker reports stable action
keys, source locations, module identity, normalized implementation digest,
delegates and detected sinks. It fails for an added, removed, changed,
multiply classified, unclassified or stale action; missing rationale/evidence;
a missing reversal, target, reachability proof or guard; product-module drift;
a stale or modified runtime policy; or a mandatory-risk invariant classified
below `protected`.

The complete surface and reviewed classification remain authoritative release
artifacts. Odoo workers do not parse those large files. They lazily load only
`protected_runtime_policy.json`, an exact generated projection of protected
entries bound to both its own digest and the canonical qualified-policy digest.
The source gate proves that projection is exact, and the Distribution image
environment binds it to the qualified image label. A regression test keeps the
worker artifact below 512 KiB.

`scripts/odoo/action_risk_inventory.py` compares the installed module set,
public model methods, routes, stored view buttons, reports, client actions,
crons and server actions with the tracked surface. Run it through Odoo shell with
`ACTION_RISK_MODE=check`; `target-finalize`, pre-production qualification and
production cut-over do this automatically.

The complete gate is enforced at four boundaries:

1. pull requests qualify source, a clean delivered registry and the
   `usl_access_control` suite without publishing an image;
2. pushes to the release branch publish only after the same qualification;
3. target finalization and pre-production compare the reconstructed runtime;
4. production preflight/gate compare checkout, candidate fingerprint, image
   label, database release identity and live registry.

Drift blocks finalization and the next release. It does not install a generic
runtime interceptor or stop an already-qualified deployment; existing
protected guards continue to enforce their boundaries.

## Shared evidence contracts

- `read_only`: exercise the action and prove no reachable local or external
  mutation sink.
- `recoverable`: perform the action, verify the changed state, execute the
  declared reversal, and verify governed state and required history.
- `protected`: prove denial for an ordinary human, denial for an Agent, denial
  for an Agent retaining its actor UID through `sudo()`, success for an
  authorized human, authorization before the side effect, and a committed
  immutable audit event carrying the action key and policy digest.
- `transport`: exercise declared targets and prove that no undeclared target or
  sink is reachable.
- `system_internal`: prove ordinary RPC/controller actors cannot reach it and
  exercise the intended internal caller.

Company-scoped actions also require cross-company denial evidence. Accounting
recovery must cover locked periods, reconciliation and retained legal history.
Provider tests use synthetic offline fixtures with
`USL_EINVOICE_LIVE_ENABLED=0` and `USL_EREPORTING_LIVE_ENABLED=0`.

Denied protected calls remain structured
`USL_PROTECTED_ACTION_DENIED` warnings because a database event in the refused
transaction would roll back. Successful protected and Agent mutations are
stored in immutable Distribution Audit events with the stable action key and
qualified policy digest.

## Boundary

The inventory covers the exact installed Odoo registry and Odoo-facing
integrations. Migration-only services, direct PostgreSQL administration,
Docker, host shell commands and Paperless internals are separate operational
trust boundaries. Any Odoo action that invokes one of those systems is in the
inventory. Arbitrary server-action execution and direct cron triggering are
themselves protected because runtime-created code cannot be exhaustively
classified in advance.
