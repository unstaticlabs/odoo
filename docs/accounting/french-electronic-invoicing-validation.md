# French Electronic-Invoicing Validation Evidence

Status date: 31 July 2026

## Verified product boundary

The distribution uses Odoo's native French Approved Platform and
electronic-document models. The USL layer governs readiness,
inactive-until-production safety and plain-language evidence; it does not
implement a second exchange or invoice engine.

The delivered state is:

- incoming UBL, CII and Factur-X documents become native draft vendor bills or
  refunds with their original document attached;
- duplicate, malformed, rejected and retryable messages remain explicit and
  idempotent;
- Accounting Managers can prepare, test, activate, monitor or pause reception;
- read-only accountants can inspect bills and evidence but cannot change the
  setup or accounting;
- reception is company-scoped;
- reception and e-reporting have separate deployment guards;
- the offline self-check retains only its status, time, configuration
  fingerprint and concise outcome;
- no provider registration, directory lookup, live retrieval, invoice
  response or e-reporting call can run while its guard is `0`.

## Durable automated coverage

Backend coverage lives in
`custom-addons/rebuild_account_migration/tests/test_einvoice_reception.py`.
It verifies:

- readiness blockers, defaults, action routing and configuration-fingerprint
  invalidation;
- repeatable, non-polluting self-check through Odoo's native decoder;
- self-check preservation across a no-op module initialization;
- UBL, CII and Factur-X invoices and credit notes;
- multiple tax rates and currencies;
- original attachments on native bills;
- provider UUID and payload duplicate detection;
- malformed, unsupported, rejected and retryable messages;
- retry idempotency and the five-attempt boundary;
- native draft review, posting, payment and reconciliation;
- mocked native approval and refusal responses, including a refusal reason;
- live-call guards and the independent e-reporting boundary;
- manager/reviewer access and multi-company isolation;
- company-scoped pause and resume;
- upgrade preservation of a valid active production configuration;
- safe removal of only untouched synthetic bills retained by the superseded
  self-check.

Source-mapping coverage in
`custom-addons/rebuild_account_migration/tests/test_rebuild_account_migration.py`
verifies that reconstruction carries the accounting contact, phone and mapped
purchase journal, derives scheme `0225` from the French company identifier, and
does not copy a proxy user, credential, KYC state or live connection claim.

Focused browser journeys live in
`custom-addons/rebuild_account_migration/static/tests/tours/einvoice_reception_tours.js`.
They cover the manager setup/self-check journey and read-only inspection of an
incoming electronic invoice. Provider boundaries are mocked in automated tests
and unexpected real requests fail the suite.

The safe fixture
`custom-addons/rebuild_account_migration/static/src/einvoice/representative_ubl_invoice.xml`
contains two synthetic lines: €100 at 20% VAT and €50 at 10% VAT, for a €175
total. It does not represent a real supplier or provider message.

## 31 July release validation

All commands used:

```bash
USL_EINVOICE_LIVE_ENABLED=0 USL_EREPORTING_LIVE_ENABLED=0 \
  scripts/odoo-dev test rebuild_account_migration \
  odoo_einvoice_hardening_final2_20260731

USL_EINVOICE_LIVE_ENABLED=0 USL_EREPORTING_LIVE_ENABLED=0 \
  scripts/odoo-dev test-tag \
  '/rebuild_account_migration:TestFrenchEinvoiceReception'

USL_EINVOICE_LIVE_ENABLED=0 USL_EREPORTING_LIVE_ENABLED=0 \
  make target-reconstruct-product

USL_EINVOICE_LIVE_ENABLED=0 USL_EREPORTING_LIVE_ENABLED=0 \
  make deploy

make product-migration-boundary
make user-docs-build
```

Results:

| Check | Result |
|---|---|
| Clean module install and complete suite | 157 post-install tests; module statistics report 166 Accounting tests, 3 OCA tests and 6 Web wrapper tests; 0 failures, 0 errors |
| Desktop and mobile JavaScript | 25 tests / 94 assertions per viewport; all passed |
| Focused reception backend suite after final changes | 13 post-install methods / 15 Odoo test statistics; 0 failures, 0 errors |
| Focused manager and reviewer browser journeys | 2 HttpCase methods; manager 4/4 steps and reviewer 6/6 steps; 0 failures, 0 errors |
| Product/migration boundary | Passed; migration add-ons and models remain outside the delivered product path |
| User documentation | MkDocs build passed without warnings |
| Static checks | Ruff, Python compilation, XML parsing and `git diff --check` passed |

Expected malformed-document logs and denied reviewer writes occurred only in
negative tests. They are not test failures.

## Canonical reconstruction and parity

`make target-reconstruct-product` rebuilt the single canonical developer/QA database,
`odoo_dev`, from the preserved Online source. It also completed the downstream
Projects restoration and removed its temporary migration module.

Accounting parity passed with:

| Object or control | Reconstructed result |
|---|---:|
| Journal entries | 5,044 |
| Posted / draft entries | 4,849 / 193 |
| Journal items | 11,871 |
| Expenses | 360 |
| Payments | 110 |
| Bank transactions | 3,046 |
| Partial / full reconciliations | 2,584 / 1,260 |
| Historical currency rates | 1,889 |
| Analytic lines | 632 |
| Assets / schedule lines / posted depreciation moves | 3 / 91 / 28 |
| Unbalanced posted entries | 0 |
| Duplicate source representations | 0 |

The controlled closed slice contains 2,046 posted moves and 4,809 lines, with
€1,064,045.02 debit and credit.

The reconstructed electronic-invoice setup contains the source accounting
contact and phone, mapped company-specific **Achats** journals and French
identifiers `0225:983982950` for Unstatic Labs and `0225:106928831` for USL
MEDIA. Because Online contains no electronic-invoice contact for USL MEDIA,
its offline form uses the reviewed central USL accounting contact. Neither
company contains a proxy identity or live connection claim.

The Accounting Manager then ran the offline self-check on `odoo_dev`. Before
and after counts were identical:

```text
account.move                     5,044 → 5,044
res.partner                         67 → 67
ir.attachment                      824 → 824
rebuild.einvoice.reception           0 → 0
```

After a repeated module upgrade, the persisted state remained:

```text
Business status                   Ready for production
Self-check                        Passed and current
Environment                       Development
Approved Platform proxy state     Not registered
Production approval               False
Incoming exchange enabled         False
E-reporting enabled               False
Pilot mode                        False
Legacy synthetic test bills       0
```

An earlier validation iteration exposed that Odoo's high-level platform import
wrapper can commit during decoding, which defeated an outer savepoint and left
one synthetic draft bill. The self-check now calls the selected native decoder
directly inside a forced rollback savepoint. Upgrade cleanup removed that
untouched legacy test bill, and dedicated regression coverage protects both
the cleanup and the zero-count invariant.

## External effects and production-only acceptance

Both live guards stayed `0` throughout installation, reconstruction, module
updates and tests. Provider success, failure, approval and refusal were mocked.
No live registration, directory query, invoice retrieval, response or
e-reporting occurred.

Software validation cannot prove:

- USL's eligibility for the selected Approved Platform;
- contract, KYC and legal-representative acceptance;
- production credentials and secret storage;
- French directory registration;
- receipt of the first real supplier invoice;
- the production backup, suspension and rollback rehearsal.

These remain explicit production activation steps, not hidden development
work. E-reporting remains a separate 2027 rollout.
