# Review B2C commerce history

The **B2C** application keeps the best available Etsy, Medusa, Stripe,
Revolut and Printful evidence in one auditable place. It is designed for
historical review and monthly controls. It does not pretend that incomplete
exports were native quotations, deliveries, payments, stock moves or invoices.
Use Odoo's normal Sales, Inventory, Purchase and Accounting applications for
new business.

## Review orders and coverage

1. Select the legal company in the company switcher, then open **B2C →
   Orders**.
2. Open an order and compare its channel, external reference, date, currency,
   totals, source rows and events.
3. Treat **Unknown**, **Pending** and incomplete coverage as real work. Treat
   **Partial** as verified monthly aggregate coverage without individual
   allocation, and **Not applicable** as an explicit decision that no direct
   relationship exists in the available evidence. A zero
   company-currency amount can mean that conversion evidence is missing; it
   does not mean that the original transaction was zero.
4. Use **B2C → Analytics** for separate views of revenue/orders, units/SKUs,
   refunds/fees, fulfilment/COGS and native stock. Do not add measures from
   different views: each view has a different accounting grain.

Order revenue is the authoritative header total. Line revenue is only the
portion supported by item-level evidence. Some legacy Medusa orders have no
defensible line detail and remain header-only.

## Review product and SKU mappings

1. Open **B2C → Operations → Product and SKU Mappings** and keep the
   **Pending** filter.
2. Compare the original SKU, listing, item name and variation with the native
   product catalog.
3. If the match is certain, select the native product, record an evidence note
   and choose **Verify Mapping**.
4. If the proposed relationship is disproved, record why and choose
   **Reject**. Use **Not applicable** with a clear note when the available
   evidence supports no defensible product match; leave only unexplained rows
   pending.

Never verify a match from a similar name alone. Verification preserves the
original provider values and does not change historical Accounting or stock.

## Review accounting and bank links

Open **B2C → Accounting Sessions → Accounting and Bank Links**. Verify a link
only when the source identifier, amount, date, currency and counterparty jointly
support it. A verified link points to existing evidence; it does not post,
cancel, reconcile or modify the linked record. A partial state means the record
is covered by a verified monthly aggregate and is not a one-to-one allocation.
Reject a disproved candidate, use not applicable when the relationship does not
exist in the locked evidence, and leave only an unexplained relationship
pending.

## Complete a monthly review

1. Open **B2C → Accounting Sessions → Monthly Sessions** and select the
   company, month and optional channel/provider.
2. Choose **Refresh Evidenced Totals**.
3. Review revenue, units, refunds, fees, COGS, margin, unallocated revenue,
   unknown amounts, conversion gaps, SKU mappings and accounting-link coverage.
4. Resolve only relationships supported by evidence and refresh again.
5. Record the remaining gaps in the review note and choose **Mark Reviewed**.
6. After accountant approval, choose **Lock Session**. Only a B2C Manager can
   unlock it, and the unlock requires an audit note.

**Reviewed** means that a person examined and explained the remaining gaps. It
does not mean every coverage value is complete. Platform Billing sessions are
separate and must not be used for B2C commerce.

## Protect sensitive evidence

Raw provider payloads may contain customer personal data. Normal B2C roles use
the structured records. Open **Restricted Provider Evidence** only when the
separate sensitive-evidence role has been granted for a documented
investigation. Do not copy raw payloads into shared spreadsheets or reports.

## Known opening-stock limitation

The source has no stock movements or dated physical count. Until Operations
approves a dated count, **Current Stock** is not an accepted opening balance.
Do not create a historical stock adjustment merely to clear the warning.
