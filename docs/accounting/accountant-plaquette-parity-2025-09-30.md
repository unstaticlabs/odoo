# Previous-accountant plaquette parity — 30 September 2025

## Scope

This control compares the canonical USL reports with `Plaquette UNSTATIC
LABS.pdf` for the first financial year, from `10/01/2024` to `30/09/2025`.
The previous plaquette is a result benchmark, not source code or a template to
copy. The target calculations use the posted native ledger for the same
company and period.

The legal reference is the French Plan comptable général, version
1 January 2025. Its account/report correspondence distinguishes production
sold (`701`–`706`) from merchandise sales (`707`) and supplier debt from other
liabilities. The Code de commerce defines the annual accounts as the balance
sheet, profit and loss and required notes taken together.

## Reconciled financial values

| Control | Previous plaquette (€) | Canonical report (€) | Difference (€) |
| --- | ---: | ---: | ---: |
| Gross assets | 71,356.21 | 71,356.21 | 0.00 |
| Depreciation / provisions | 1,676.05 | 1,676.05 | 0.00 |
| Net assets / total passif | 69,680.16 | 69,680.16 | 0.00 |
| Net fixed assets | 8,754.44 | 8,754.44 | 0.00 |
| Equity | 57,222.98 | 57,222.98 | 0.00 |
| Total debt | 12,457.18 | 12,457.18 | 0.00 |
| Turnover | 129,188.62 | 129,188.62 | 0.00 |
| Operating products | 129,190.02 | 129,190.02 | 0.00 |
| Operating charges | 63,009.32 | 63,009.32 | 0.00 |
| Operating result | 66,180.70 | 66,180.70 | 0.00 |
| Financial products | 80.63 | 80.63 | 0.00 |
| Financial charges | 116.35 | 116.35 | 0.00 |
| Current result before tax | 66,144.98 | 66,144.98 | 0.00 |
| Corporate income tax | 9,922.00 | 9,922.00 | 0.00 |
| Total products | 129,270.65 | 129,270.65 | 0.00 |
| Total charges | 73,047.67 | 73,047.67 | 0.00 |
| Net result | 56,222.98 | 56,222.98 | 0.00 |
| Value added | 85,322.30 | 85,322.30 | 0.00 |
| Gross operating surplus | 67,856.84 | 67,856.84 | 0.00 |
| Cash-flow capacity | 57,899.03 | 57,899.03 | 0.00 |

## Reconciled ratios

The ratios now display a value, unit and concise formula. They use the same
statement rows as the screen and export.

| Ratio | Previous | Canonical | Definition |
| --- | ---: | ---: | --- |
| Fixed-asset coverage | 6.55 | 6.55 x | Permanent resources / net fixed assets |
| Repayment capacity | 370.53 | 370.53 x | Cash-flow capacity / financial debt |
| Gross operating margin | 0.53 | 0.53 x | Gross operating surplus / net turnover |
| Commercial profitability | 0.44 | 0.44 x | Net result / net turnover |
| Economic profitability | 6.42 | 6.42 x | Net result / net fixed assets |
| Financial profitability | 0.98 | 0.98 x | Net result / equity |
| Operating working-capital importance | 0.01 | 0.01 x | Operating current assets less operating current liabilities, excluding corporate-income-tax debt, / net turnover |

The previous sheet labels some denominators as turnover including VAT. The
canonical report deliberately uses net accounting turnover because mixed VAT
rates and non-taxed sales cannot be reconstructed by applying one assumed
rate. On this benchmark, both conventions round the working-capital ratio to
`0.01`; the canonical formula is explicit in the report.

## Material semantic corrections

- Account `455100` (€156.26) is shown under **Emprunts et dettes financières
  diverses**, not supplier payables. The total debt and total passif do not
  change.
- Accounts `701000`/`701099` feed production sold. Merchandise sales use `707`
  net of `7097`. The corrected commercial margin is `-6,288.77` and production
  is `129,188.62`; value added and later SIG totals stay unchanged.
- The €1.40 of other operating products is a visible line.
- Total products `129,270.65` and total charges `73,047.67` are visible before
  net result.
- Calculated equity totals no longer duplicate all class 6/7 accounts already
  exposed under **Résultat de l’exercice**.
- Six misleading imported labels are corrected through governed presentation
  records, without rewriting ledger master data: `281540`, `281830`, `511100`,
  `627100`, `631200` and `768000`.

## Source-code normalization retained for traceability

The native target uses normalized target account codes for several historical
accounts. Amounts and source identifiers remain drillable.

| Previous code | Target code | Meaning |
| --- | --- | --- |
| 431000 | 438700 | Social body / accrued social item |
| 511213 | 511100 | Etsy transfer awaiting collection |
| 512101 | 512001 | Bank |
| 512103 | 512006 | Revolut |
| 512104 | 512004 | Revolut |
| 512105 | 508101 | Revolut Savings EUR |
| 512106 | 508103 | Revolut Flex USD |
| 512107 | 508102 | Revolut Flex GBP |
| 455101 | 455100 | Associate current account |

The `508` targets remain `asset_cash` in Odoo and are presented as
availability because they are bank savings/flex balances in this ledger. This
preserves the previous plaquette's `57,479.97` cash total while keeping the
normalized account and its original source identity visible in drill-down.

## Annual-package boundary

The generated French annual PDF contains a cover, contents, preparation
status, balance sheet, profit and loss, SIG/CAF and management ratios. It is
marked **prepared by the company — not professionally attested**.

The previous accountant's professional attestation is intentionally not
reproduced. Accounting-method narratives, inventory work, estimates, required
legal notes and any professional opinion remain controlled closing inputs.
This is a deliberate authority boundary, not a financial difference.

## Authoritative references

- ANC, *Recueil des normes comptables françaises — Plan comptable général,
  version du 1er janvier 2025*:
  https://www.anc.gouv.fr/files/anc/files/1_Normes_fran%C3%A7aises/Reglements/Recueils/PCG_Janvier2025/Recueil-NF-Janvier-2025.pdf
- Code de commerce, annual-account structure:
  https://www.legifrance.gouv.fr/loda/id/LEGISCTA000034161774
- Bpifrance Création, SIG definitions:
  https://bpifrance-creation.fr/encyclopedie/piloter-lentreprise/finance-pilotage-economique/comprendre-calculer-soldes
