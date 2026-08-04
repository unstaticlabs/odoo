# USL Documents Accounting

`usl_documents_accounting` is the focused bridge between the Paperless-backed
Documents workspace and native Accounting records. It adds authorized archive
links and evidence actions without copying Paperless binaries into Odoo or
making the archive authoritative for ledger state.

General archive behavior belongs in `usl_documents`; Accounting-specific
models and views belong here. One-shot source reconstruction remains under
`migration/documents_archive` and is absent from the normal add-ons path.

Run the focused backend suite with:

```bash
scripts/odoo-dev test usl_documents_accounting odoo_test_usl_documents_accounting
```
