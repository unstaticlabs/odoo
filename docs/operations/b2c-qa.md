# B2C QA guide

## Required gates

Run static checks before database work: manifest/dependency validation, Python
and shell lint, XML schema/external-ID checks, parser unit tests, French
catalogues, and `make product-migration-boundary`.

Database qualification requires clean install, update, repeated update, import,
validation, repeated import/validation, temporary-module finalization, targeted
Odoo tests, and three fresh reconstructions:

1. `PROFILE=no-documents make qa` for direct archive parsing without Documents;
2. `PROFILE=documents-smoke make qa` for deterministic sample links;
3. a source-complete full qualification, followed by `PROFILE=full make qa` for
   the remotely audited target.

All runs require unique Compose projects and the two live electronic-invoice
guards set to zero. A cached full environment is acceptable only when its
manifest exactly matches the source dump, runtime, product modules, and
reconstruction digest.

## Manager journey

Sign in as Valentin and confirm the B2C manager role. Check:

- 304 canonical orders and overlap evidence on an Etsy order;
- 235 Etsy rows/237 units plus 222 Medusa rows/225 units, with pending
  original SKU aliases and no fabricated Medusa provider line IDs;
- two Medusa lines on display order `1617586399`, with quantities two and one;
- separate payment/refund/fee and fulfilment/COGS pivots;
- a Stripe record with blank row ID but a stable unique evidence key;
- a Revolut refund linked to its original payment;
- a negative Printful refund with VAT evidence;
- a monthly session with explicit mapping, link, conversion, and unallocated
  revenue coverage;
- native product, sale, stock, purchase, Accounting, and attachment drill-downs;
- zero historical native stock movements and the not-evidenced opening-stock
  warning.

Verify and reject disposable test aliases only; do not change historical
Accounting evidence.

## Read-only and unauthorized journeys

Sign in as Prosper with the B2C reviewer role. Lists, pivots, graphs, searches,
and drill-downs must work, while create/edit/delete, mapping actions, session
actions, link changes, and restricted payloads are unavailable. A user with no
B2C group must have no B2C model or menu access. Repeat with a second allowed
company to prove records never cross company rules.

## Release blockers

The environment may be functionally ready for B2C audit while the Distribution
still has unrelated strict source-truth gates. Report them without weakening
the tests. B2C itself is not production-ready until final archive links are
complete, physical opening stock is supported by an approved dated count, all
required historical company-currency amounts have evidence, and unresolved
SKU/accounting relationships have an explicit reviewed disposition.

For the 26 August source-complete rehearsal, the visible queue is:

- 109 pending SKU aliases and no verified or rejected mappings yet;
- 306 foreign-currency events without complete company-currency amounts;
- 35 orders without evidenced currency and 304 orders without a verified
  accounting link;
- 2,386 event/fulfilment records without a verified accounting relationship;
- 9 verified and 31 pending B2C archive-attachment links;
- no approved dated physical opening-stock count.

These counts are source-bound acceptance controls. Resolve them through
evidence-backed review or an approved explicit disposition; do not make the
software infer a relationship merely to reduce the queue.
