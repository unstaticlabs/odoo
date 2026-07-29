# Accounting evidence in the document archive

Odoo remains authoritative for invoices, bills, entries, tax/reporting context,
payment state, decisions, and retention policy. Paperless preserves the
received evidence and its searchable derivatives. Accountant access is limited
to documents explicitly marked accounting evidence for an allowed company.

Received supplier invoices, bank/tax evidence, signed material, payroll, and
legally significant files are immutable originals. Rotation, merge, page
removal, OCR regeneration, or correction must create a derivative or later
Paperless version; it must not overwrite the received file. The Odoo
relationship may record the version supporting a particular posting.

Paperless API v10 returns file versions newest first and marks the initially
received file with `is_root`. Odoo therefore treats the first entry as current,
labels the `is_root` entry **Received original**, and retains every
version-specific checksum. A new version changes the document cache's current
checksum without losing the received-original checksum or stable Odoo
relationships.

Posted invoice PDFs, finalized reports, FEC exports, and tax packages may need
both an Odoo operational copy and a Paperless archival copy. This is deliberate:
the relationship records source `odoo_generated`, checksum, filename, user,
company, and time. A later regeneration must not silently replace the archived
legal output.

Archiving an existing Odoo attachment does not delete it. Any future
deduplication project requires checksum verification, retention approval, a
successful restore rehearsal, and an explicit migration audit. Deleting an Odoo
record or unlinking evidence never deletes the archive original.

Accounting restore acceptance requires opening representative vendor-bill,
customer-invoice, journal-entry, tax, and generated-output evidence from Odoo,
matching recorded checksums, and re-running accountant/non-accountant and
multi-company access checks.

The optional `usl_documents_accounting` bridge extends the generic relationship
contract to installed tax declaration and accounting closing-period models. It
does not alter posting, reconciliation, tax computation, or report semantics.
