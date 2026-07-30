# Try the Documents application

This is a task-first guide for the isolated local QA stack. Use synthetic files
only.

## Start QA and sign in

From the repository:

```bash
make documents-qa-up
make documents-qa-bootstrap
make documents-qa-status
```

Open:

- Odoo: `http://127.0.0.1:18080`
- Paperless: `http://127.0.0.1:8010`

QA passwords are all `admin`:

| Role | Odoo username | Paperless username |
| --- | --- | --- |
| Administrator | `admin` | `archive-admin` |
| General documents user | `documents-user` | `documents-user` |
| Accountant | `documents-accountant` | `documents-accountant` |
| HR reviewer | `documents-hr` | `documents-hr` |
| Other-company restricted user | `documents-restricted` | `documents-restricted` |

Start as `admin/admin`, open **Documents**, and use the seeded synthetic
archive. Do not run `down --volumes`, `scripts/odoo-dev reset`, or a bare
`docker compose --profile paperless up`.

## 1. Find text inside a document

1. In Documents, search for `Net VAT payable`.
2. Choose the suggested **Tax & reporting** tag.
3. Set type to **Tax filing** and company to **My Company**.
4. Open **Synthetic VAT return — July 2026**.

You should get one authorized result. The preview contains the searched words.
The panel immediately explains the date, correspondent, type, tags, company,
and Odoo links. Healthy synchronization and checksum messages are absent.

Try Back and Forward:

- Back closes the document and keeps the same filters and list position.
- Forward reopens the same document.
- Reload keeps the selected document and selected version.

## 2. Add tags without leaving the document

1. Open **External mailroom intake — needs review**.
2. Click **Add tag** beside its current tags.
3. Type part of a tag name and use the keyboard to select it.
4. Type a harmless new tag such as `CEO QA reviewed` and create it inline.
5. Remove that tag with its small remove action.

The picker should remain practical without a separate edit screen. Paperless is
updated first and Odoo reads the result back. If Paperless is unavailable, the
visible value must roll back instead of pretending the change saved.

Use **Edit** only for title/date/correspondent/type. Those values should also
appear in Paperless after saving.

## 3. Understand and edit automatic classification

Use the top **Tags**, **Correspondents**, and **Document types** menus.

Open a tag such as **Tax & reporting**. The form should explain:

- how documents match;
- which words or pattern Paperless looks for;
- whether matching ignores letter case.

Change only a harmless synthetic rule. Ingest a synthetic text file containing
that pattern in Paperless, wait for processing/synchronization, and confirm the
assigned tag appears in Odoo. Odoo should not show a made-up confidence score.

## 4. Map a correspondent to a Contact

1. Open the top **Correspondents** menu.
2. Open **USL People Operations**.
3. Review the suggested Odoo Contact.
4. Use **Use suggested Contact** or **Not this Contact**.
5. For a mapped correspondent such as **Northstar Retail**, open its Contact
   and then its Documents smart button.
6. Leave **Mobile Capture Gateway** unmapped as an archive-only correspondent.

Mapping should never create an Odoo Contact automatically, link a document, or
grant access. A user in another company must not see an inaccessible Contact
mapping.

## 5. Upload or link from a vendor bill

1. Open the draft vendor bill with reference
   `USL-DOCS-CEO-QA-BILL`.
2. Its one smart button should say **1 Documents**.
3. Click it. Documents opens with a removable **Linked record** facet and the
   seeded supplier invoice.
4. Remove that facet. The rest of the authorized archive becomes searchable and
   an unlinked document offers **Link to this record**.
5. Upload a harmless PDF or text file with a distinctive sentence.

During upload, the same page shows pending/processing state. Odoo says archived
only after Paperless succeeds. The durable bill relationship then appears, and
no extra `ir.attachment` binary is created.

On any supported record with no archived links, the same smart button is
**Upload**. It still opens Documents with both upload and link-existing
available; there is no second Archive button.

## 6. Reuse a duplicate

Upload the exact same file again, including after it has become an older file
version.

The existing Paperless root should be reused. No new archive root and no Odoo
binary copy should appear. The implementation compares the checksum with the
current file and the complete mirrored version history.

## 7. Link one archived file to several records

1. From the supplier invoice detail, remove the record facet and choose another
   legitimate synthetic Odoo record.
2. Click **Link to this record**.
3. Confirm both relationships appear under **Linked records**.
4. Remove one relationship.

There should still be one Paperless binary and one remaining relationship.
Removing a link never moves the document to Trash.

## 8. Use file history confidently

1. Open **Contracts & legal**.
2. Open **Northstar Retail — Signed services agreement**.
3. The current file is visible under **File versions** without expanding
   anything.
4. Open **Earlier versions (N)** and preview the received original.
5. Under **More**, upload a harmless replacement.
6. Restore an earlier version as current and confirm the warning.

The selected old file becomes a new current version. **Received original** and
all earlier files remain previewable/downloadable. No version is overwritten.

## 9. Compare shared Smart Views with Paperless

1. In Odoo, open **Configuration > Smart views**.
2. Open **Contracts & legal**, **Banking**, or **Tax & reporting**.
3. Confirm it shows a stable Paperless Saved View identity.
4. In Paperless as `archive-admin/admin`, open Saved Views and find the same
   shared view.
5. Make a harmless archive-native change, synchronize, and check Odoo again.

Company, confidentiality, accounting-evidence, HR, or linked-record
restrictions are labelled as Odoo policy and are not claimed to be identical
Paperless Saved Views. Personal Odoo views remain private to their owner.

## 10. External ingestion and Trash

1. Upload a harmless file directly in Paperless.
2. Wait for processing and Odoo synchronization (normally within five minutes).
3. Find it in **Needs review**, classify it, and link it.
4. Move that Paperless document to Trash.
5. Synchronize and open its linked Odoo record.

The document should be **In Trash**. Its relationships remain, normal
edit/download actions are unavailable, and an authorized **Restore document**
action returns the same stable identity and links.

The seeded **Retention review sample — in Trash** lets you test Restore without
creating another item. Run `make documents-qa-bootstrap` afterward to return it
to the intended demo state.

## 11. Check each role

- `documents-user/admin` sees authorized general and accounting material, but
  not HR/private/other-company items.
- `documents-accountant/admin` sees the seeded accounting examples—supplier
  invoice, expense, bank statement, and VAT filing—without unrelated private or
  HR material.
- `documents-hr/admin` also sees **Camille Martin — July payroll evidence**.
- `documents-restricted/admin` gets an intentional empty archive for the
  synthetic other company and cannot infer a known title through search,
  preview, download, version, or copied URL.

Search/session state is stored per Odoo user. Switching accounts must not carry
the prior user's query into the next account.

## 12. Check an outage

The automated safe exercise is:

```bash
make documents-qa-acceptance
```

It stops only the isolated QA Paperless web service, proves an Odoo vendor bill
still opens, restores Paperless, resumes synchronization, and verifies counts
do not change.

For interactive review, keep the vendor bill open while that target is running.
During the Paperless stop, Documents shows one actionable unavailable message;
the bill and the rest of Odoo remain usable. After recovery, retry without
creating duplicate documents or relationships.

## 13. Prove backup and restore

Run:

```bash
make documents-qa-recovery-test
```

The exercise backs up Odoo and Paperless independently, starts each without the
other, restores both into a new isolated Compose project/database/volume set,
and checks document/version/link counts, representative checksums, previews,
permissions, and orphans. Container startup alone is not a passing result.

## Stop QA safely

```bash
scripts/documents-stack qa stop
```

This preserves QA databases, filestore, Paperless media, versions, exports,
users, and relationships.

## Report a problem

Include the task number, URL, username, active company, document title, exact
message, and a screenshot. Do not attach confidential files. Also report
anything that works technically but feels confusing, slow, or unlike normal
Odoo.
