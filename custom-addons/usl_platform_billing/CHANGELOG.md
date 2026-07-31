# Changelog

## saas~19.2.1.1.2

- Show every eligible open incoming bank transaction by default; matching
  rules now rank recommendations instead of hiding manual choices.
- Replace the single bank link with auditable allocations, so one receipt can
  settle several payouts and one payout can be settled by several receipts.
- Fix **Import selected as payouts** and **Link selected transactions** for
  normal Operator users, including direct selection toggles and refreshed
  session results.
- Reconcile pooled and partial receipts through the pinned OCA API without
  changing posted bank move balances.
- Convert existing bank links to allocations during upgrade and extend
  backend, migration and Chromium coverage.
- Use Odoo's native analytic mixin on platform configuration so the
  Administrator form and analytic widget load correctly.
- Add a guarded, deterministic local QA bootstrap for the delayed, pooled,
  partial, foreign-currency and role-boundary journeys.

## saas~19.2.1.1.1

- Show every billing session by default while retaining the optional Open
  filter.
- Name new sessions from their period using the historical French format.
- Accept harmless workflow defaults sent by Odoo forms without allowing direct
  state changes.
- Cover fresh operator session creation and delayed settlement in browser QA.

## saas~19.2.1.1.0

- Keep posted sessions open when platform payments are delayed.
- Allocate one pooled bank receipt across payouts from several sessions.
- Warn, without blocking, when posting omits an active platform.
- Replace generic Accounting access with opt-in Reader, Operator and
  Administrator roles.
- Use partner payment terms unless the session has an explicit due-date
  override.

## saas~19.2.1.0.0

- Replace the Studio/server-action bootstrap with a standalone application.
- Add company-scoped platform, session and payout models.
- Generate native invoices, commission bills and optional compensation
  entries.
- Reconcile incoming bank transactions through pinned OCA APIs without
  rewriting posted move lines.
- Add mixed-currency summaries, evidence attachments, chatter, permissions,
  matching audit fields and automated tests.
- Add the isolated, revisioned historical restoration stage.
