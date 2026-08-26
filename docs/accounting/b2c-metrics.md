# B2C metric and accounting-link contract

## Metric grains

B2C reports separate grains so a join cannot duplicate totals:

| Surface | Grain | Additive measures | Coverage control |
| --- | --- | --- | --- |
| Revenue, orders, country | one canonical `b2c.order` | order count; transaction- and company-currency revenue, subtotal, shipping, discount, tax, refund, fee, net | amount completeness, company conversion, accounting and document link states |
| Units and SKU | one `b2c.order.line` source row | quantity and evidenced line revenue | original SKU/listing, mapping state, line revenue coverage |
| Payments, refunds, fees | one provider event | amount, negative refund, fee, tax, net | provider identifier, order/accounting/bank/native-payment link, conversion state |
| Fulfilment and COGS | one provider fulfilment/refund row | provider cost, shipping cost, tax/VAT, negative refunded COGS | order/accounting/native-stock links, completeness and conversion |
| Stock | native `stock.move` and `stock.quant` | done movement quantity and current on-hand quantity | source baseline is zero; opening count is not evidenced |

Order revenue is not reconstructed from line detail. Etsy lines cover item
revenue only. A separately checksum-locked Medusa sold-items export now provides
222 lines for all 96 current Medusa orders, but it has no immutable provider
line IDs and its line sums do not always equal header totals. The 35 remaining
legacy-only Medusa orders still have headers only. Order reports therefore own
total revenue. SKU drill-down is explicitly “evidenced line revenue” and shows
`line_revenue_coverage_percent` plus positive or negative unallocated header
revenue. It must never be labelled total revenue when coverage is not exactly
100%.

## Amount and margin rules

Transaction amounts remain in their original currency. Company amounts are
populated only when the currency matches company currency, a processor provides
an evidenced conversion, or a restored historical rate is explicitly applied.
Pending conversions remain zero in company-currency aggregations and are
counted as unknown coverage; today's rate is never substituted.

Refund fields and refunded fulfilment/COGS values are negative. Gross margin at
the monthly session grain is:

`company revenue + company refund - company fees - company COGS`

It is an evidenced subtotal, not an assertion of completeness. Missing lines,
conversion, COGS, mappings, and accounting links are reported separately.

## Accounting immutability

An accounting link is metadata. Verification can associate a B2C record with an
existing immutable move, move line, bank transaction, payment, or supporting
attachment. It cannot post, cancel, resequence, reconcile, or modify those
records. Import validation fingerprints moves, lines, partial/full
reconciliations, bank lines, native payments, and all native sale/purchase/stock
transactions before and after every pass.

The raw source contains 1,467 `account.full.reconcile` rows. Accounting's
operational restoration correctly retains the 1,340 IDs referenced by restored
journal lines; the other 127 rows are empty/unreferenced source records. B2C
preserves and checks the Accounting target fingerprint and does not redefine
that established parity rule.
