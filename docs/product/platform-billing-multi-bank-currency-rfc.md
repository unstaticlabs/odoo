# RFC: mixed bank currencies in a billing session

Status: proposed; not implemented by the bank-import/commission fix.

One economic platform can receive USD into Wise and EUR through Remitly in the
same month. The current single-bank-currency session requires separate sessions
for these receipts. Keep that supported workflow until the following changes
can be qualified together.

## Proposed model

- Keep one company and accounting period per session. Turn the session bank
  currency into a default import filter, not an accounting denomination.
- Keep each allocation's immutable receipt currency and original amount.
  Represent session and payout bank totals grouped by currency; never add USD
  and EUR into one Monetary field.
- Permit a payout to have allocations in several bank currencies. Keep the
  platform amount per allocation as the common measure for capacity checks.
- Retain reference valuation by default for foreign-currency bank accounts.
  Any extension of effective valuation must use the certified liquidity
  journal-item balance, prorated to the allocated share, not a new spot rate.
  Fees, pooled receipts and partial allocations need explicit treatment.

## Migration and acceptance

Preserve existing session currency values as import defaults and retain all
posted documents, allocations and receipt amounts. Replace every consumer of
the old single-currency bank total before relaxing its allocation constraint:
session summaries, payout summaries, wizard candidates, exports and previews.
Qualify an upgrade with existing USD-only and EUR-only sessions; compare every
ledger and allocation value before/after. Then test mixed receipts, partial and
pooled settlements, reset/reimport, multi-company denial and certified bank
fingerprints. Do not merge or rewrite existing economic sessions automatically.

This is a coordinated stored-data and UI change, not a removal of the currency
validation check in isolation.
