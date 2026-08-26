# B2C QA guide

## Required gates

Run static checks before database work: manifest/dependency validation, Python
and shell lint, XML schema/external-ID checks, parser unit tests, French
catalogues, and `make product-migration-boundary`.

Database qualification requires clean install, update, repeated update, import,
validation, repeated import/validation, temporary-module finalization, targeted
Odoo tests, and one automatically removed, source-complete disposable
reconstruction. After that rehearsal passes, reconstruct and validate the
single canonical `odoo_dev`. `no-documents` and `documents-smoke` remain useful
developer profiles, but neither is release evidence.

All runs require unique Compose projects and the two live electronic-invoice
guards set to zero. A cached full environment is acceptable only when its
manifest exactly matches the source dump, runtime, product modules, and
reconstruction digest.

## Manager journey

Sign in as Valentin and confirm the B2C manager role. Check:

- 304 canonical orders and overlap evidence on an Etsy order;
- 235 Etsy rows/237 units plus 222 Medusa rows/225 units, with original SKU
  evidence, explicit mapping dispositions and no fabricated Medusa provider
  line IDs;
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

## Accepted source limitations and release inputs

The environment may be functionally ready for B2C audit while the Distribution
still has unrelated strict whole-source gates. Report them without weakening
the tests.

The completed source-backed B2C disposition is:

- 109 aliases: nine verified by an exact unique internal reference and 100
  explicitly not applicable; zero unexplained pending mappings;
- 59 order lines linked to those exact products and 398 explicitly not
  applicable;
- 180 verified monthly session-to-move links, 81 unique bank links, and 14
  direct identifier relationships covering 10 events;
- across orders, payments and fulfilments, 10 direct verified records, 927
  honestly aggregate-covered records, 1,449 not-applicable records, and zero
  unexplained pending records;
- all 40 B2C source files in final Documents, all 2,893 immutable evidence rows
  linked to their archived source file, and every source file linked to a
  durable B2C business record;
- no pending per-event conversion claim: where no defensible individual
  company-currency conversion exists, the state is not applicable and the
  company-currency truth remains at verified aggregate session level;
- 35 legacy Medusa orders still have no evidenced currency and remain visible
  as such; and
- no approved dated physical opening-stock count exists.

The last two items are honest source/input limitations, not queues that software
may erase. The physical count planned for 30 September 2026 is a separate later
opening-stock operation and must not create historical stock activity.
