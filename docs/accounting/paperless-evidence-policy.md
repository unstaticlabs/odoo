# Accounting evidence in the document archive

Odoo remains authoritative for invoices, bills, journal entries, expenses,
tax/reporting context, payment state, posting decisions, legal company, and
retention policy. Paperless preserves the received evidence, extracted text,
searchable derivative, and file history. Accountant access is limited to
documents explicitly marked as accounting evidence for an allowed company.

## Originals and versions

Received supplier invoices, bank/tax evidence, signed material, payroll, and
legally significant files are immutable originals. Rotation, merge, page
removal, OCR regeneration, redaction, or correction must create a derivative or
later Paperless version; it must not overwrite the received file.

Odoo mirrors stable Paperless version identities and version-specific
checksums. The current file is always shown in **File versions**. **Received
original** identifies the initially retained evidence even after replacements.
Restoring an older file downloads that authorized version server-side and
creates a new current Paperless version; the complete sequence remains intact.

**Download original** is the primary evidence action. Paperless's processed
searchable PDF is secondary under **More** and is described as a derivative.
Checksums and permission-check timestamps stay in technical details unless an
integrity or access problem requires action.

When one version legally supports a posting, tax declaration, bank
reconciliation, or signed decision, the Odoo relationship should retain that
version identity rather than relying only on whichever file is current later.

## Classification is not authorization

Tags, correspondents, document types, matching rules, and Paperless Saved Views
help users find evidence. They never grant accounting, company, HR, or private
access. Accounting views compose archive metadata with Odoo's
`accounting_evidence`, company, confidentiality, relationships, and record
rules.

The accountant role sees approved accounting evidence only. It does not infer
titles, thumbnails, tags, Contacts, or file IDs for unrelated internal, HR,
private, or other-company documents. Every preview, download, and version route
authorizes again in Odoo.

## Odoo-generated outputs

Posted invoice PDFs, finalized reports, FEC exports, tax packages, signed
outputs, and similar final records may require:

- an Odoo copy for normal operational behavior; and
- an immutable Paperless archival copy.

This is the accepted deliberate-duplication case. Source `odoo_generated`,
filename, checksum, submitting user, company, time, and Odoo relationship make
the two roles explicit. A later regeneration must create a new version or
separate finalized output; it must not silently replace a legally significant
archived file.

Archiving an existing Odoo attachment does not delete it. Any future
deduplication requires checksum verification, retention approval, successful
restore rehearsal, and an explicit migration audit. Deleting an Odoo record or
removing one relationship never deletes the Paperless original.

## Trash and retention

An accounting document moved to Paperless Trash remains represented on its
linked Odoo records as **In Trash**. Authorized Restore returns the same stable
archive identity and relationships. Permanent deletion is a separate audited
administrator action and must satisfy the applicable retention policy.

## Restore acceptance

Acceptance is not “both containers start.” A representative exercise must:

1. restore Odoo database and complete filestore;
2. restore Paperless database, media/originals, and data/search state
   independently;
3. compare current and received-original checksums;
4. resolve Odoo relationships to the same Paperless roots and versions;
5. verify accountant, general, HR, private, and multi-company isolation;
6. open vendor-bill, customer-invoice, journal-entry, expense, tax, legal, HR,
   project, and generated-output evidence;
7. report missing and orphaned identities.

The synthetic QA recovery rehearsal on 30 July 2026 restored 39 document roots,
37 Odoo relationships, and 54 mirrored file versions. Representative preview,
checksum, permissions, and orphan checks returned `integrity_ok=True`.

The optional `usl_documents_accounting` bridge extends the generic relationship
contract to installed tax declaration and accounting closing-period models. It
does not alter posting, reconciliation, tax computation, or report semantics.
