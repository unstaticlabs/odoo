# Enable Banking import integrity

The adapter adds booked transactions to native bank journals. Existing journal
items, reconciliation, lock dates and imported history remain governed by Odoo.
It does not initiate payments or certify bank statements.

## Provider contract and observed coverage

The public Enable Banking coverage directory was inspected on 12 November 2026:
Fortuneo / ARKEA DIRECT BANK in France is listed for personal account information
in sandbox and production. This proves listed coverage, not a successful consent
or a transaction payload. No Fortuneo consent or account response was obtained
for this implementation.

Primary references, inspected on the same date:

- [Own-account activation](https://enablebanking.com/docs/api/linked-accounts/):
  linking one's own accounts can activate restricted production access. This is
  separate from the subsequent account-information authorization.
- [Transaction schema](https://enablebanking.com/docs/api/reference/#transaction):
  `entry_reference` is optional. When present it is immutable and unique among
  accounts with the same identification hashes, across authorization sessions;
  it is not globally unique. `transaction_id` is a detail-lookup identifier and
  can change on a repeated fetch.
- [Session account schema](https://enablebanking.com/docs/api/reference/#sessionaccount):
  the UID belongs to a session; identification hashes identify the account
  globally and include aliases derived from its possible account numbers.
- [Transactions endpoint](https://enablebanking.com/docs/api/reference/#get-account-transactions):
  date bounds are inclusive and paging uses a continuation key.

The schema's optionality is not evidence that Fortuneo omits a field. The
human-consented pilot must inspect actual field availability privately before
claiming live import qualification.

## Identity and multiplicity

A local account retains its stable identity when any returned identification
alias matches in the same company and currency. All aliases are retained.
Session renewal changes only the session UID and availability metadata. A
resource omitted from a renewed session stays in Odoo with its history and is
marked unavailable. Different returned currencies remain separate account
resources even if the account-number hash is shared.

The import identity combines company, stable account identity and the bank's
entry reference. Native OCA then adds its journal/account prefix. A separate
read-only provenance record binds that identity to the created native bank line,
booking/value dates, currency and financial digest. Repeating an identical bank
reference skips an existing line; conflicting financial facts refuse the batch.
Two references with equal dates, amounts and descriptions produce two lines.
An existing native line without adapter provenance is linked only when its
exact adapter-derived immutable import ID, journal/company, currency, booking
date and signed amount all match. The line stays untouched; provenance records
**Matched existing**. A financial mismatch refuses the entire batch.

No alternate field in the published account-information transaction schema has
the documented cross-fetch uniqueness of `entry_reference`. A missing reference
therefore stops only that account's automatic batch, with its checkpoint and
existing lines unchanged. A session-local `transaction_id`, creditor reference,
amount/date hash or page ordinal is not substituted. In particular, a repeated
page with anonymous identical rows is indistinguishable from extra legitimate
occurrences without a stronger provider guarantee. Native manual entry and file
import remain available on the journal. Their identities may differ from this
adapter's provenance, so automatic imports must start after a period already
handled manually unless the exact source identities have been reconciled.
Amount/date similarity alone never justifies collapsing transactions. A real payload or provider guarantee
can justify a future multiplicity/provenance fallback; synthetic fixtures cannot.

## Financial mapping and atomicity

Only `BOOK` entries become bank lines. The API's other known statuses are
excluded; an unknown or missing status is a visible response error. The bank's
booking date is preserved as a calendar date. Value date stays in provenance
and is never silently substituted for missing booking date. Debit/credit
indicators determine the sign. Amounts must be finite, representable without
loss in Odoo's numeric field, and valid for the account currency's precision.
A currency mismatch refuses the batch rather than creating an invented rate.

All transaction pages are retrieved and validated before native line creation.
A savepoint covers the complete booked-transaction import, its provenance and
checkpoint. A later-page failure, repeated cursor, conflict or transaction
validation failure leaves no partial batch and does not advance either schedule
or successful-import checkpoint. Balances are ancillary: a failed or malformed
balance response leaves previously retrieved balances and their original dates
untouched, with a separate refresh warning. Complete valid booked transactions
still import and advance their checkpoint. A subsequent valid balance refresh
replaces the cache and clears the warning; an expired balance consent also
prompts renewal without undoing a complete booked import. Company/connection and account row locks
serialize imports and reconnects; SQL uniqueness backs the provenance key.

OCA's existing scheduler dispatches the provider. Its preview path fetches data
without changing lines, balances or checkpoints. The manual pull wizard opens
native bank lines. Native journal mapping may be deliberately changed by an
authorized accountant; old lines keep their original journals, and provenance
prevents their reimport into the replacement journal.

Provider-reported balances retain the bank's type, name, currency, reference
date, change time and retrieval time. They are displayed separately from Odoo's
ledger. Available balances do not become opening/closing statement controls.

## Access and activation

Accounting users import through their native rights and allowed companies.
Accounting managers configure the application and consent through the same
company boundaries. Managed identity, consent and result fields are not writable
by ordinary RPC calls. The signing key comes from a company-scoped mounted
secret; it is never stored in Odoo, sent to the browser or logged. Callback state
is single-use, short-lived, and bound to its initiating user and company.

No additional Agent-specific business permission is introduced. Native
accounting, delegation and irreversible-action controls keep their existing
meaning. Application registration, terms, account linking and bank consent
remain an explicit human activation step; the implementation does none of them.
