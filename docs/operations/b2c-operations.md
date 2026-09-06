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

Use the coverage states consistently: **Verified** is an exact direct match;
**Partial** is honest monthly aggregate coverage without individual allocation;
**Not applicable** means the relationship does not apply or the locked evidence
contains no matching ledger/catalog fact; **Pending** is an unexplained gap;
and **Rejected** is a disproved proposal.

## SKU review

1. Open **B2C → Operations → Product and SKU Mappings** and filter `Pending`.
2. Compare the immutable source SKU, listing, name, variation, and restricted
   evidence with the native catalog. Never infer a match from name similarity.
3. Select the verified product and choose **Verify Mapping**, or record a reason
   and choose **Reject**. Use **Not applicable** with a clear evidence note when
   no defensible catalog match exists; leave only genuinely unexplained evidence
   pending.
4. Confirm affected order lines and the original SKU remain visible.

## Accounting and bank links

Create a link only when the source identifier, amount, date, currency, and
counterparty jointly support it. Choose the appropriate revenue, refund, fee,
payout, bank, clearing, supplier-cost, COGS, or supporting-evidence type. A
verified link points to existing Accounting evidence; it never edits that
evidence. Reject a disproved candidate, use not applicable where the
relationship honestly does not exist, and leave only an unexplained
relationship pending. Never turn monthly aggregate coverage into a direct
order allocation.

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

## One-off native reconstruction

The historical reconstruction runs once, through
`migration/internal/b2c-restore`, and is then removed. Each stage is idempotent
and refuses to continue on drift, so a stage can be repeated safely.

```bash
migration/internal/b2c-restore install
migration/internal/b2c-restore import
migration/internal/b2c-restore catalog
migration/internal/b2c-restore native-history-dry-run
migration/internal/b2c-restore native-history
migration/internal/b2c-restore finalize
```

`import` rebuilds the canonical evidence from the frozen source. `catalog`
creates the reviewed products, their variants, one alias per channel identity
and the bills of materials, then points every order line at its variant.
`native-history` promotes the customer sales into native Sales, Purchase, stock
and manufacturing records, and refuses to run unless the evidence still matches
its pinned fingerprints.

Marketing and prototyping spend is corrected separately, because the promotion
asserts that it changes no Accounting at all:

```bash
USL_B2C_MARKETING_COST_MODE=dry_run migration/internal/b2c-restore reclassify-marketing
```

It moves what the print supplier billed for orders that were never sales out of
purchases of goods and into samples, one entry per month, and declines any month
inside a closed financial year.

Run the whole sequence, verify it, and finalize before warehouse work resumes.
The promotion proves a repeat by re-reading every record it created against the
same evidence, so ordinary operations afterwards make that proof fail — which is
deliberate: after finalization the reconstruction is history.
