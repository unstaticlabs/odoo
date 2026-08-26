# B2C operator and accounting-session workflow

## Personas

- **B2C reviewer** can inspect orders, source coverage, events, fulfilments,
  mappings, sessions, and native drill-downs but cannot write anything.
- **B2C operator** can verify or reject mappings and evidence links, refresh and
  review sessions, and lock a reviewed session.
- **B2C manager** can also configure channels, unlock sessions, and grant the
  restricted provider-evidence role.

Raw provider evidence may contain customer PII. Grant its separate role only
for a documented investigation. Do not export it into shared analytical files.

## SKU review

1. Open **B2C → Operations → Product and SKU Mappings** and filter `Pending`.
2. Compare the immutable source SKU, listing, name, variation, and restricted
   evidence with the native catalog. Never infer a match from name similarity.
3. Select the verified product and choose **Verify Mapping**, or record a reason
   and choose **Reject**. Leave ambiguous evidence pending.
4. Confirm affected order lines and the original SKU remain visible.

## Accounting and bank links

Create a link only when the source identifier, amount, date, currency, and
counterparty jointly support it. Choose the appropriate revenue, refund, fee,
payout, bank, clearing, supplier-cost, COGS, or supporting-evidence type. A
verified link points to existing Accounting evidence; it never edits that
evidence. Reject a disproved candidate and leave unknown relationships pending.

## Monthly session

1. Open **B2C → Accounting Sessions → Monthly Sessions** and choose the company,
   first day of month, optional channel, and optional provider.
2. Select **Refresh Evidenced Totals**. Review revenue, units, refunds, fees,
   COGS, margin, unallocated revenue, unknown amounts, conversion gaps, mapping
   gaps, and link coverage.
3. Drill through each separate report grain. Do not compare a mixed-currency
   transaction total with a company-currency ledger total.
4. Resolve only supported mappings and links; refresh again.
5. Record remaining discrepancies in the review note and choose **Mark
   Reviewed**. A reviewed session is not a declaration that unknown coverage is
   zero.
6. Lock the session after accountant approval. Locked sessions are immutable;
   only a B2C manager may return one to reviewed state, with an audit note.

Platform Billing sessions are separate and must not be used for Etsy, Medusa,
Stripe, Revolut, or Printful commerce.
