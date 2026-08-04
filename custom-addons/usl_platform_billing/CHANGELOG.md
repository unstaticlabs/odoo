# Changelog

## saas~19.2.1.2.1

- Show a green **No Payment Required** status for generated miscellaneous
  entries instead of Odoo's invoice-oriented **Not Paid** status.
- Give Platform Billing Administrators the native analytic-accounting access
  required by the platform distribution widget and hide the tab from roles
  that cannot use it.
- Grant the named `valentin` user the application Administrator role in the
  isolated QA bootstrap without changing generic Accountant access.

## saas~19.2.1.2.0

- Value bank-created foreign-currency payouts at their effective bank rate
  before posting invoices, commission bills and compensation entries.
- Keep bank-first settlements free of immediate exchange gains or losses while
  preserving Odoo reference rates for payouts recorded before payment arrives.
- Show the valuation method, company-currency bank basis and effective rate on
  payout records, with backend and Chromium coverage for USD 1,000 received as
  EUR 700.
- Keep foreign-currency compensation entries labelled in their platform
  currency while retaining the exact company-currency accounting value.

## saas~19.2.1.1.4

- Keep incomplete bank-import drafts out of the settlement selector until
  their platform currency and original amount are completed.
- Align the French operator guide with ranked bank suggestions, pooled and
  delayed settlements, and explicit Platform Billing roles.

## saas~19.2.1.1.3

- Import bank receipts as incomplete draft payouts, then complete platform,
  reference, currency and original payout amount on the session.
- Simplify the bank-import screen around label, amount, date and selection;
  keep matching details optional and show suggestions with a small marker.
- Put validation first and consistently color payout, bank and accounting
  document status badges.
- Show the effective product, partner and bank accounts on platform
  configuration and validate their accounting types before generation.
- Use the restored service-revenue and sales-commission accounts, plus the
  bank journal proven by restored payout history, in local QA data.
- Hide bank transactions whose actual currency differs from the session bank
  currency, and keep the local import demo repeatable after a completed run.

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
