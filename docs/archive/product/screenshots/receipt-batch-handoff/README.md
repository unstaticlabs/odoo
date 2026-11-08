# Receipt and batch QA screenshots

Captured 14 October 2026 (Europe/Paris), after final tested source, both scoped
upgrades, runtime seal and asset compilation, using an isolated Odoo server.
The signed-in user is a synthetic employee, not an administrator. All people,
companies, expense amounts, receipts and messages are disposable QA fixtures.

- `receipt-handoff.png`: native receipt website and attach controls after the
  one-click browser journey. The actual functional evidence separately records
  one new tab, one authenticated CSRF POST and a synthetic provider document
  request without a referrer or opener.
- `queued-retry.png`: a genuinely queued request aged 31 minutes for the fixture,
  with processing explicitly paused. Retry is available; it does not enable
  processing or prove the live backlog's cause.
- `batch-add-only.png`: the native add dialog has Add to Batch and Cancel;
  submission remains in the batch's own review action.

These images show final layout and copy, not proof of submission or download.
Native batch submission and its persisted actor/date/chatter were verified
separately in the running QA server. Each image was personally inspected for
private data, credentials, real contacts, document contents, browser chrome and
paths. PNG chunks contain only IHDR, IDAT and IEND; no embedded metadata is
published. Private login and browser sidecars are excluded.
