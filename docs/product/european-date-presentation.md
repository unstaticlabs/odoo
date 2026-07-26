# European Date Presentation

## Product decision

USL Accounting displays complete numeric dates as `DD/MM/YYYY`. A date such as
10 June 2026 is therefore `10/06/2026`, never `06/10/2026`.

The convention applies to date and datetime fields in normal Odoo lists and
forms, Accounting screens, filters, generated documents and exports.
Human-readable English dates in contextual prose use day-first European
ordering, for example `10 Jun 2026`. Time-zone conversion and the underlying
stored date or UTC datetime are unchanged.

## Language policy

Interface language and date convention are separate product choices:

- English (`en_US`) keeps the English interface and uses European dates.
- French (`fr_FR`) keeps the native French interface and the same
  `DD/MM/YYYY` numeric convention.
- Other installed languages keep their own configured Odoo convention.

The `rebuild_account_migration` module governs the English and French
`res.lang` date formats. Its backend date service maps only English-US
human-readable rendering to European English ordering, and its shared field
formatters keep the year visible. This avoids hardcoded user changes and avoids
a fork-level patch to Odoo's web client.

## Upgrade and regression contract

Module installation or update reapplies the supported-language configuration.
The backend regression test verifies Odoo/QWeb formatting, and the frontend
test verifies numeric dates, datetimes and native human-readable dates.

Any deliberate future change must update both tests and this decision record.
Per-view US month-first overrides are not permitted.
