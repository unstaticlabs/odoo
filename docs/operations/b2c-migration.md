# B2C migration and reconciliation

## Required source inputs

The Odoo Online dump and filestore remain the primary source and must match the
locked dump SHA-256. Current Medusa headers exist there, but Medusa sold-item
detail does not. Before running the stage, place the separately supplied
provider export at
`usl-online-dump/supplemental/b2c/medusa-sold-items-2026-08-05.csv`. This
private, Git-ignored file is part of the canonical source package and must
match SHA-256
`e8308c402a63d4c4fd7ee066c8a59daeba7b00cd66f421221191cec50418550a`.
Missing or changed supplemental evidence is blocking; it is never silently
treated as an empty line set or read from a competing repository-artifact
path.

## Safety and stage order

The one-shot add-on is available only at
`migration/b2c_restore/addons/usl_b2c_restore`. Its initial canonical-record
pass runs after Accounting, Identity, and Product. Relationship finalization
runs only after Accounting, Product, Projects, and the complete Documents
archive exist. It reads the restored source with `accounting_source_ro` and a
read-only transaction. It parses source filestore objects directly, so
`no-documents` and `documents-smoke` are valid developer iteration profiles; a
release qualification requires every final archive and business link.

Always use a runtime resolved by `migration/manage`, with both
electronic-invoice guards at zero. Never open
`odoo_online_source_saas_19_3` with target Odoo code.

The internal stage installs the temporary registry, imports, validates,
repeats safely when requested, uninstalls the temporary add-on, removes its
rows, columns, and XML IDs, and runs the product/migration boundary.
`migration/manage` places this stage after Product restoration. Do not use
`-u all`.

When Documents is enabled, canonical reconstruction repeats the idempotent B2C
pass after full archive ingestion and before final migration cleanup. The
release pass requires every one of the 40 source-package files to exist by exact
checksum in Documents and to have at least one durable B2C business link.
Missing, changed, duplicate, ambiguous, or unlinked source documents and native
business targets abort the migration.

## Import contract

The source dump SHA-256 must be
`ad313e28586fafa27a4f6a266df57080456613dff1c8c2c6d7e012732bf633b1`.
The manifest fixes 39 dump-backed files by name, source ID, SHA-1, size, MIME
type, and filestore path, plus the separately supplied Medusa line export by
SHA-1 and SHA-256. Every CSV additionally fixes its exact ordered header and
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
- all 180 critical journal moves linked to the correct provider/month session;
- direct, aggregate, not-applicable, and pending B2C coverage reported
  separately, with no unexplained post-cutoff gap;
- 40 exact Documents files and durable links to all 2,893 provider-evidence
  rows;
- all 109 SKU aliases either verified by exact internal reference or explicitly
  not applicable, with no unexplained pending mapping;
- named supplier-document states, residuals, reconciliation edges and native
  attachments; and
- the deliberately separate physical opening-stock input.

Finalization must prove a second import changes no counts or fingerprints, then
remove the temporary module and all technical provenance. Business identifiers,
canonical provider evidence, and verified business links remain product data.
