# European Date Presentation

## Product decision

USL Accounting presents dates day-first. A complete numeric date such as
10 June 2026 is therefore `10/06/2026`, never `06/10/2026`.

Normal lists and read-only fields keep Odoo's compact convention: `10 Jun` when
the date is in the current year and `10 Jun 2025` otherwise. Editable numeric
fields, filters, generated documents and exports use the complete
`DD/MM/YYYY` form. Time-zone conversion and the underlying stored date or UTC
datetime are unchanged.

Custom interactive filters use Odoo's `DateTimeInput` with the explicit
`dd/MM/yyyy` presentation format. Native HTML `input type="date"` controls are
not permitted because their visible order is selected by the browser and can
silently revert to month-first despite the Odoo language configuration.

The readable PDF and XLSX report surfaces use `DD/MM/YYYY`, including report
periods, detail-date columns, metadata sheets, headers and generation
timestamps. ISO `YYYY-MM-DD` remains allowed only in machine metadata, raw
audit data, RPC payloads and technically safe filenames.

## Language policy

Interface language and date convention are separate product choices:

- English (`en_US`) keeps the English interface and uses European dates.
- French (`fr_FR`) keeps the native French interface and the same
  `DD/MM/YYYY` numeric convention.
- Other installed languages keep their own configured Odoo convention.

The `rebuild_account_migration` module governs the English and French
`res.lang` date formats. Its backend date service maps only English-US
human-readable rendering to European English ordering. This avoids hardcoded
user changes and avoids a fork-level patch to Odoo's web client.

## Upgrade and regression contract

Module installation or update reapplies the supported-language configuration.
The backend regression tests verify Odoo/QWeb formatting, report-export date
cells and the absence of native HTML date controls from the report client. The
frontend tests verify numeric dates, datetimes, report-filter serialization,
day-first ordering and native current-year omission.

Any deliberate future change must update both tests and this decision record.
Per-view US month-first overrides are not permitted.
