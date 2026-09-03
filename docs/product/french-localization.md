# French Product Language

French is a maintained product language of the USL Odoo Distribution, not an
automatic fallback. Product add-ons own their French strings in `i18n/fr.po`.
When an add-on changes an upstream record, it must also own the translated
value: changing the English name alone can leave an obsolete upstream French
translation in the database.

Use familiar French accounting language consistently:

| Product concept | French term |
| --- | --- |
| Accounting application | Comptabilité |
| Customer invoicing activity | Facturation |
| Bank Matching | Rapprochement bancaire |
| General Reconciliation | Lettrage général |
| Accounting Hygiene | Hygiène comptable |
| Post / Posted | Comptabiliser / Comptabilisé |
| Expense receipt | Justificatif |
| Vendor bill | Facture fournisseur |
| Closing | Clôture |
| Tax package | Liasse fiscale |
| French Approved Platform | Plateforme agréée |
| Human Resources | RH |
| Review / Reviewed | Vérifier / Vérifié |
| Upload | Importer |

Translate from the business context, not from an isolated English word.
Accounting actions use *rapprochement* or *lettrage*; document metadata and
search predicates use *correspondance*. A supplier bill is never a *projet de
loi*, and the company name `Unstatic Labs` is never translated. Prefer natural
French sentence structure even when an Odoo view divides the sentence around a
dynamic field; translate the surrounding fragments together and protect the
rendered result with a French-context test.

Keep protocol names, identifiers and third-party product names unchanged when
translation would reduce precision. Validate catalogues with
`make french-translations` before committing changed user-facing strings.
The supported `make deploy` and `make rebuild` workflows overwrite installed
USL catalogue terms so corrected product translations also reach an existing
development target. Upstream terms are untouched; manual overrides of these
maintained USL terms are intentionally replaced.
For a release-wide audit, export the installed product modules from a clean,
disposable registry, then run
`python3 scripts/check_fr_translations.py custom-addons --reference-po <export.po>`.
This additional comparison detects new source terms that have not yet been
added to any maintained catalogue.
