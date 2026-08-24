# B2C migration and reconciliation

## Safety and stage order

The one-shot add-on is available only at
`migration/b2c_restore/addons/usl_b2c_restore`. Run it after Accounting,
Identity, Product, and the source attachment inventory. It reads the restored
source with `accounting_source_ro` and a read-only transaction. It parses source
filestore objects directly, so `no-documents` and `documents-smoke` are valid
iteration profiles; a release qualification still requires all final archive
links.

Always use an isolated Compose project and set both electronic-invoice guards
to zero. Never open `odoo_online_source_saas_19_3` with target Odoo code.

```bash
COMPOSE_PROJECT_NAME=usl-odoo-b2c-<worktree-id> \
USL_ONLINE_DUMP_DIR=/absolute/path/to/usl-online-dump \
USL_EINVOICE_LIVE_ENABLED=0 USL_EREPORTING_LIVE_ENABLED=0 \
scripts/b2c-restore
```

The command installs the temporary registry, imports, validates, repeats safely
when requested, uninstalls the temporary add-on, removes its rows/columns/XML
IDs, and runs the product/migration boundary. `scripts/target-reconstruct`
places the same stage after Product restoration. Do not use `-u all`.

When Documents is enabled, canonical reconstruction repeats the same idempotent
B2C pass after archive ingestion and before final migration cleanup. This
refresh links only exact, unique native attachments materialized by the archive;
files without such a target remain explicitly pending in the discrepancy
report.

## Import contract

The source dump SHA-256 must be
`0b9916db4807206f63b654bd2933ac89b0aab30ba7e0a1004edc4c060490238f`.
The source attachment manifest fixes name, source ID, SHA-1, size, MIME type,
and filestore path; every CSV additionally fixes its exact ordered header and
SHA-256. Any changed checksum, schema, missing file, extra CSV column, or parser
baseline aborts the stage.

Canonical precedence is Etsy item evidence (50), current Medusa (30), legacy
Medusa (10). All overlapping source rows remain immutable evidence, but one
external business order key creates one `b2c.order`. Blank Stripe row IDs use a
file-checksum/row/digest key; they are never collapsed. Revolut refunds require
their original payment. No fuzzy SKU is applied.

## Reconciliation and finalization

The private machine-readable reports are
`artifacts/b2c-restore/source-target-parity.json` and
`mapping-and-discrepancies.json`. They contain no committed source payloads.
Validate at least:

- archive/file/schema baselines and canonical counts;
- 46 source-mapped templates/variants and 45 source `product.value` rows;
- source-mapped warehouse/location/route/rule/operation-type counts;
- zero native source sales, purchases, payments, stock history, quants, and
  valuation layers;
- Accounting moves/lines and debit/credit by account, journal and partner;
- partial/full reconcile and bank-state fingerprints;
- B2C month/channel totals and order/payment/refund/fulfilment link coverage;
- every pending SKU, conversion, archive attachment, accounting relationship,
  and physical opening-stock input.

Finalization must prove a second import changes no counts or fingerprints, then
remove the temporary module and all technical provenance. Business identifiers,
canonical provider evidence, and verified business links remain product data.
