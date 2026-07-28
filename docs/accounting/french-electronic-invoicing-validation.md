# French Electronic-Invoicing Validation Evidence

Status date: 28 July 2026

## Automated evidence

The durable backend suite is
`custom-addons/rebuild_account_migration/tests/test_einvoice_reception.py`.
It covers:

- fresh module installation and module upgrade;
- French company identifiers, scheme `0225`, journal and honest readiness;
- UBL invoice, UBL credit note, CII and Factur-X;
- two VAT rates, EUR and USD;
- native draft vendor bill/refund creation and original-file preservation;
- posting, payment and payable reconciliation;
- same-message idempotency and same-payload duplicate prevention;
- malformed input, rejected delivery, five-attempt recovery boundary,
  authentication and temporary-provider guidance;
- mocked provider success/failure;
- Accounting Manager and read-only accountant permissions;
- multi-company evidence isolation;
- reception-only scheduled jobs, disabled auto-registration/e-reporting and
  hard external-call guards;
- absence of migration, reconstruction, parity and debug menus from daily
  Accounting roles.

The browser tours are
`custom-addons/rebuild_account_migration/static/tests/tours/einvoice_reception_tours.js`.
They exercise the Accounting Manager from readiness through the offline
document, native bill and evidence, and the read-only accountant from reception
evidence to the non-postable draft.

## Safe fixture

`custom-addons/rebuild_account_migration/static/src/einvoice/representative_ubl_invoice.xml`
is a synthetic supplier invoice. At runtime the buyer identifiers and dates are
replaced with the current company. It contains €100 at 20% VAT and €50 at 10%
VAT for a €175 total. It never represents a real supplier or provider message.

## Commands and results

Validation uses an isolated Compose project and disposable PostgreSQL volume,
not `odoo_dev`, `odoo_online_source_saas_19_2` or the shared candidate
databases. The final installation database was
`odoo_peppol_release_20260728` in Compose project
`usl-peppol-qa-20260728`; both names are disposable test evidence, not
deployment targets. The user-facing QA database is
`odoo_peppol_qa_20260728` in the same isolated project.

The following final checks passed on 28 July 2026:

| Check | Command scope | Result |
|---|---|---|
| Fresh installation and reception backend suite | test image on `odoo_peppol_release_20260728`, `--init=rebuild_account_migration --test-tags=/rebuild_account_migration:TestFrenchEinvoiceReception --without-demo=true` | 99 modules installed; 7 methods / 9 Odoo assertions; 0 failures, 0 errors |
| Explicit module upgrade and prior reception regression | test image on `odoo_peppol_release_20260728`, `--update=rebuild_account_migration --test-tags=/rebuild_account_migration:TestRebuildAccountMigration.test_french_einvoice_reception_is_offline_traceable_and_deduplicated --without-demo=true` | Upgrade completed; 1 method / 3 Odoo assertions; 0 failures, 0 errors |
| Browser acceptance | Chromium test image on `odoo_peppol_defaults_test2_20260728`, `--test-tags=/rebuild_account_migration:TestFrenchEinvoiceReceptionBrowser` | Accounting Manager 9/9 steps and read-only accountant 6/6 steps; 0 failures, 0 errors |
| Deployed QA acceptance | `scripts/odoo-dev bootstrap-einvoice-qa`, followed by in-app browser review | French commercial chart, EUR, 20% and 10% VAT, €175 total, original UBL, normal Confirm action, Odoo PA Demo invoice, manager controls and read-only visibility verified |
| Python lint | `ruff check` on the reception model, new suite and modified regression suite, using the current Ruff container | Passed |
| Python syntax | `python3 -m compileall -q` on the same Python files | Passed |
| XML syntax | `xmllint --noout` on the cron data, readiness views and representative UBL | Passed |
| Patch hygiene | `git diff --check` | Passed |

All Odoo runs used the test image with the worktree's `custom-addons` mounted,
the repository's OCA paths mounted read-only, cron disabled, and both live
guards at their default `0`. Provider success, authentication failure and
temporary failure were mocked; no live registration, lookup, retrieval,
delivery or e-reporting request was made.

Resolved validation iterations are retained here for honesty:

- the first isolated install could not resolve OCA dependencies because the
  worktree's OCA directories were empty; the existing repository OCA trees
  were subsequently mounted read-only;
- early backend runs exposed and fixed country-neutral tax setup, scheduler
  user permissions, inactive test currency, CII payment-account setup,
  retained-attachment retry access and embedded Factur-X extraction;
- the first browser runs exposed and fixed a changed status selector, durable
  readiness action view binding and the company-name cell click target;
- the first QA bootstrap exposed legacy shell argument ordering, a transaction
  boundary around native PDF generation, and a generic USD chart; the final
  bootstrap uses the French commercial chart and EUR;
- browser tours were first skipped in the runtime image because it intentionally
  lacks browser-test dependencies; they then passed in the repository test
  image with Chromium and `websocket-client`;
- Ruff `0.12.2` could not parse the repository's newer rule set; the current
  Ruff image then identified two comment-style findings, which were fixed
  before the final passing run.

## Not verified by software tests

- USL's production identity acceptance and applicable provider terms;
- approved-platform production credentials and support route;
- live French directory registration and effective date;
- delivery of the first real supplier invoice;
- production backup/rollback rehearsal and Accounting Manager acceptance.

Those are deliberate activation prerequisites, not hidden implementation work.
