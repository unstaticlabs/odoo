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
| Post / Posted | Comptabiliser / Comptabilisé |
| Expense receipt | Justificatif |
| Closing | Clôture |
| Tax package | Liasse fiscale |
| French Approved Platform | Plateforme agréée |

Keep protocol names, identifiers and third-party product names unchanged when
translation would reduce precision. Validate catalogues with
`make french-translations` before committing changed user-facing strings.
