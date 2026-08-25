# Expense Batch transition

The Canada draft transition runs after native expense reconstruction inside
the isolated Accounting migration add-on. It is not installed in the final
product registry.

## Preconditions

- `625600 Missions` exists for the company;
- native analytic accounts `Projet: SBFH prod` and `Epic: Canada 2026` are
  unambiguous;
- active reusable Products resolve unambiguously for `TRANS`, the foreign
  `FOOD` variant and `GIFT_NOVAT`;
- live e-invoicing and e-reporting flags remain disabled.

## Deterministic result

Eligible draft `CA26` expenses are linked to `SBFH — Canada 2026`. Uber/taxi,
meal/snack and gift descriptions are mapped to the corresponding reusable
category. An unrecognized description stays on `CA26` and is reported as
ambiguous. The Batch applies the Missions account and combined SBFH/Epic
analytic distribution. Missing evidence remains missing and nothing is
submitted. Matching account and analytic values are recorded as Batch-inherited;
only effective differences remain exceptions. The transition links the draft
recordset once, removes only imported taxes whose country conflicts with the
company fiscal country, and writes one summarized Batch audit message. Native
posting validation remains enabled.

The transition deliberately repairs the imported draft data instead of
overriding `account.move` tax-country validation at runtime. Likewise, the QA
bootstrap selects an already-active native payment method instead of
reactivating archived accounts during Batch posting.

Non-draft signatures—Product, account, analytics, move and state—are captured
before and checked after the operation. Finally `AUS26`, `CA26`, `LPASUM26`
and `BCN2602` are archived. A rerun repairs provenance on an already-created
draft Canada Batch, consolidates only exact transition-generated context
messages and then becomes a no-op. User-authored chatter is never removed.
The canonical reconstruction reruns this transition after Product restoration
so imported Product activity cannot reactivate the archived trip categories.
The final product-boundary check verifies the four archives, all 19 inherited
Canada lines from the authoritative source dump, the unchanged ambiguous Zen
Kyoto Product classification and the absence of false context exceptions.

For local acceptance, `make expense-batch-qa-bootstrap` creates the separate,
synthetic **QA — Mixed payment Batch** in `odoo_dev`. It never changes the
factual Canada Batch and refuses to replace later-stage QA expenses. Synthetic
company-paid lines explicitly use an active outbound bank payment method rather
than whichever reconstructed method happens to sort first.

Run the focused migration tests, then the isolated
`accounting-dev-reset`, `accounting-dev-import` and
`accounting-dev-validate` sequence. Retain the emitted transition JSON with
the reconstruction evidence; do not store migration provenance on operational
models.
