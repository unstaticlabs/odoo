# Changelog

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
