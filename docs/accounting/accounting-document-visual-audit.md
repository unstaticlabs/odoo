# Accounting Document Visual Audit

## Acceptance contract

This is the permanent one-by-one audit for the 31 active human-readable
accounting PDFs. Baseline describes the previous generic v1 output. Treatment
is implemented in the shared report session and `accounting_statement.v2`.
Evidence is regenerated from the isolated `odoo_dev` QA database; when a
source report has no populated rows, the pack labels its deterministic fixture
as synthetic. FEC is machine output and the inactive historical profit-and-loss
alias is covered by canonical Compte de résultat compatibility tests.

The legal structure follows Code de commerce L123-13: Actif and Passif are
separate and equity is distinct. L123-19 prevents presentation netting. The
account classification reference is the ANC PCG collection effective
1 January 2026.

## One-by-one record

| # | Report | Baseline finding | Implemented treatment | Acceptance evidence |
|---:|---|---|---|---|
| 01 | Balance générale | Dense account wall | PCG class headings and exact class subtotals; debit, credit and equality control | `01-balance-generale.pdf` |
| 02 | Grand livre | Account context disappeared in long runs | Account blocks retain opening, movements, closing and continuation heading | `02-grand-livre.pdf` |
| 03 | Journal comptable | Every journal had equal weight | Native journal-type sections, journal totals and equality control | `03-journal-comptable.pdf` |
| 04 | Grand livre auxiliaire | Partner/account boundaries were weak | Partner then account hierarchy, opening/movement/closing subtotals | `04-grand-livre-auxiliaire.pdf` |
| 05 | Relevé client | Customer balance competed with ledger detail | Customer-led section, concise dated movements and explicit statement total | `05-releve-client.pdf` |
| 06 | Écritures ouvertes | Receivables/payables mixed | Separate Clients/Fournisseurs, partner groups, due date and residual totals | `06-ecritures-ouvertes.pdf` |
| 07 | Balance âgée clients | Buckets were misnamed and undifferentiated | Correct ageing buckets, overdue progression, partner and grand totals | `07-balance-agee-clients.pdf` |
| 08 | Balance âgée fournisseurs | Payable exposure lacked emphasis | Mirrored ageing grammar with total supplier exposure | `08-balance-agee-fournisseurs.pdf` |
| 09 | Bilan | Actif/Passif interleaved | Dedicated Actif and Passif pages, distinct equity, exact totals and control | `09-bilan.pdf` |
| 10 | Compte de résultat | Duplicate intermediate hierarchy | Consolidated sections; operating, current and net results form the visual spine | `10-compte-resultat.pdf` |
| 11 | TVA et taxes | Statutory and ledger evidence mixed | Explicit statutory-grid and VAT-ledger sections with base/tax/account evidence | `11-tva-et-taxes.pdf` |
| 12 | Taxes par compte puis taxe | No account context | Account sections and exact subtotals above tax detail | `12-taxes-compte-taxe.pdf` |
| 13 | Taxes par taxe puis compte | No tax context | Tax sections and exact subtotals above account detail | `13-taxes-taxe-compte.pdf` |
| 14 | État récapitulatif TVA UE | Declaration dimensions were flat | Period/country sections; partner and VAT number remain adjacent; declaration totals | `14-etat-recapitulatif-tva-ue.pdf` |
| 15 | Ventes OSS | Destination exposure was flat | Destination-country and tax-treatment hierarchy with base/tax totals | `15-ventes-oss.pdf` |
| 16 | Importations OSS | Import scheme was ambiguous | Import/destination hierarchy with explicit base and tax | `16-importations-oss.pdf` |
| 17 | Rapprochement bancaire | Exceptions were buried | Journal sections, status column, unresolved residuals visually retained | `17-rapprochement-bancaire.pdf` |
| 18 | Change | Currency evidence was hard to scan | Separate ledger, realised and unrealised sections; original/company/residual currency columns | `18-change.pdf` |
| 19 | Flux de trésorerie | Four decisive figures looked generic | Compact encaissements, décaissements, surplus and closing-cash ladder | `19-flux-tresorerie.pdf` |
| 20 | Synthèse de gestion | Money and ratios shared one grammar | Key figures and ratios remain separate; units/formulas stay adjacent; undefined ratios are blank | `20-synthese-gestion.pdf` |
| 21 | Compte de résultat analytique | Analytic dimensions lacked nesting | Analytic group hierarchy with account/product contribution and dimension totals | `21-compte-resultat-analytique.pdf` |
| 22 | Analyse analytique | No governed PDF | Server-recomputed current pivot, repeated row header, landscape segmentation and totals | `22-analyse-analytique.pdf` |
| 23 | Registre des immobilisations | Asset category and close control were weak | Account/category schedule with gross, depreciation, net and grand total | `23-registre-immobilisations.pdf` |
| 24 | Immobilisations par compte | Summaries lacked a final control | Compact account summaries and grand gross/depreciation/net total | `24-immobilisations-compte.pdf` |
| 25 | Plan d’amortissement | Schedule read like a ledger | Asset sections, date/status schedule and asset totals | `25-plan-amortissement.pdf` |
| 26 | Charges et produits constatés d’avance | Charges/products were mixed | Schedule type/account sections with period, status and exact subtotals | `26-charges-produits-constates-avance.pdf` |
| 27 | États financiers français | Professional package was incomplete | Governed cover, contents, preparation status, Actif, Passif, result, SIG/CAF, ratios and non-attestation notice | `27-etats-financiers-francais.pdf` |
| 28 | Bilan détaillé | Sides shared one flow | Dedicated Actif/Passif pages, Brut/Amortissements/Net/comparison and equality control | `28-bilan-detaille.pdf` |
| 29 | SIG et CAF | Calculation ladder was visually flat | Separate SIG/CAF sections, concise secondary formulas, emphasized intermediate and final results | `29-sig-caf.pdf` |
| 30 | Liasse fiscale française | Field state/source hierarchy was weak | Form/section hierarchy; code beside label; amount/text/status/source; unresolved-first evidence | `30-liasse-fiscale-francaise.pdf` |
| 31 | Dossier de revue de clôture | Generic table | Status cover, controls, declarations, unresolved actions, evidence and lock-date conclusion | `31-dossier-revue-cloture.pdf` |

## Final gate

The review pack is accepted only when all files pass structural parsing,
embedded-font and Unicode extraction checks, required PDF/A checks, and a
batched PNG inspection with no clipping, orphaned headings, split totals,
collisions, placeholder text or misleading zeros. Screen/PDF/readable-XLSX
hierarchy reconciliation and exact XLSX Audit Data remain automated tests.

## Sources

- Code de commerce, articles L123-12 to L123-24:
  https://www.legifrance.gouv.fr/codes/id/LEGIARTI000006219304/
- ANC, Recueil des normes comptables françaises, version 1 January 2026:
  https://www.anc.gouv.fr/files/anc/files/1_Normes_fran%C3%A7aises/recueil/2026/Recueil-PCG-Janvier-2026.pdf
