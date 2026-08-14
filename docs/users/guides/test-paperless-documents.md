# Try the Documents application

This is a task-first guide for the isolated local QA stack. It uses synthetic
files; the complete Odoo Online archive is handled only by the isolated,
deterministic migration workflow documented in the operations guide.

## Start QA and sign in

From the repository:

```bash
make documents-qa-up
make documents-qa-bootstrap
make documents-qa-status
```

Open:

- Odoo: `http://127.0.0.1:18080`
- Paperless: `http://127.0.0.1:18010`
- Pocket ID: `http://pocket-id-documents.localhost:18110`

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

The passwords above stay available only for fast local role testing. A separate
`documents-sso-user` exists for the production authentication path. Generate
that person's one-hour Pocket setup link with:

```bash
python3 scripts/pocket_id_dev.py \
  --env-file .documents-qa-sso.env \
  one-time-link documents-sso-user
```

Use it to enroll the Pocket user, then choose Pocket ID on both the Odoo and
Paperless login screens. The first Paperless login creates that individual
account through the supported OIDC flow. Run `make documents-qa-bootstrap`
again to verify/map its new numeric Paperless identity and synchronize object
permissions. Odoo and Paperless use separate clients but the same immutable
person. Pocket groups do not add document access; the user must still see
exactly the documents allowed by their Odoo company and Documents role.
This first-login exercise belongs only to the isolated synthetic QA identity.
Canonical `odoo_dev` and reconstructed targets pre-provision governed users and
their mappings during `make target-finalize`.
Both **Pocket ID** buttons must reach the same local Pocket tenant without a
callback error. QA registers Paperless's exact HTTP callback; pre-production
uses the equivalent HTTPS callback and disables Paperless password login.

## 1. Find text inside a document

1. In Documents, type `heliotrope`.
2. Choose the first suggestion, **Search everywhere for: heliotrope**. It
   becomes a removable **Search everywhere** facet.
3. Click the top **Accounting** tag chip. It is applied on top of the search,
   leaving **Alpine Office Supplies — Invoice SI-2026-0715**.
4. Open the document and confirm the preview contains the OCR-only sentence.

You should get one authorized result. The preview contains the searched words.
The panel immediately explains the date, correspondent, type, tags, company,
and Odoo links. Healthy synchronization and checksum messages are absent.

Open the search dropdown. It must be Odoo's normal three-column menu:

- **Filters** includes My uploads, Needs review, Linked/Not linked,
  Accounting, HR, availability, date ranges, and Custom Filter;
- **Group By** includes Company, Correspondent, Type, Employee, Privacy,
  Review status, and document/archive month;
- **Favorites** saves the current search for the signed-in user.

There is no separate **More filters** form.

The initial suggestions deliberately keep only frequent choices: Search
everywhere, Title, Document content, Tags, Correspondent, Type, Company, and
Date. Use **Filters > Add Custom Filter** for archive identity, source,
privacy, review state, availability, mapped Contact, employee, or a Paperless
custom field. Every choice remains a normal removable Odoo facet and still
passes through Odoo authorization.

The small chips below the search bar are deliberate shortcuts:

- the first group comes from the active Smart View, such as **Last 30 days** or
  **Group by employee** in HR;
- the colored group is made from the most-used tags the current user can
  access;
- every shortcut composes with the search facets already present.

Select two colored tags. Odoo should show one search facet listing both tags
with **or**, and the results may carry either tag. Removing one colored chip
updates that same facet instead of leaving a hidden filter behind.

Try Back and Forward:

- Back closes the document and keeps the same filters and list position.
- Forward reopens the same document.
- Reload keeps the selected document and selected version.

At the top of the document panel, use **Open Preview** to open the authorized
file preview in a separate tab. **Open in Paperless** opens the same document
with the user's own archive identity for advanced work. The links should not
appear when the user cannot safely access that destination.

## 2. Add tags without leaving the document

1. Open **External mailroom intake — needs review**.
2. Click **Add tag** beside its current tags.
3. Type part of a tag name and use the keyboard to select it.
4. Type a harmless new tag such as `CEO QA reviewed` and create it inline.
5. Remove that tag with its small remove action.

The picker should close on selection, Escape, or an outside click and remain
practical without a separate edit screen. Paperless is updated first and Odoo
reads the result back. If Paperless is unavailable, the visible value must
roll back instead of pretending the change saved.

The prominent document title is editable directly in the panel header. There
is no Classification heading or Edit/Save/Cancel mode. Click the
correspondent, document type, or date directly and confirm the field briefly
shows that it is saving before the final value appears in Paperless. Document
dates must display as `DD/MM/YYYY` everywhere. Try:

- selecting an existing Paperless correspondent;
- choosing an Odoo Contact from **Search Contacts** to create or reuse its
  mapped Paperless correspondent;
- creating an archive-only correspondent inline;
- creating a new document type inline;
- choosing and clearing a date through Odoo's calendar picker, then confirming
  the result remains day-first after closing and reopening the document.

As a Documents administrator, click **Company** on an unlinked document and
choose another company that is active in Odoo's company switcher. The new
company should save inline and access should be recalculated immediately.
Ordinary users must see Company as read-only. A document linked to a business
record in the original company must refuse the move until that relationship is
removed; the error must leave the original company unchanged.

Documents that still need a human decision show a short review banner at the
top of the side panel. Check the company, useful classification, and linked
business records, then select **Mark reviewed** without opening the technical
record. The banner disappears when the review is complete. If safe archive
access or the legal company is unresolved, the same banner explains the exact
blocking action and does not allow a false completion. Only Documents managers
finish reviews; other users may prepare the metadata and see who must complete
the decision.

## 3. Understand and edit automatic classification

Use the top **Tags**, **Correspondents**, and **Document types** menus.

First prove that live creation works:

1. Create `CEO QA test tag` in Tags.
2. Create `CEO QA test correspondent` in Correspondents.
3. Confirm each saves without an Odoo Server Error and also appears in
   Paperless.
4. Delete the two empty test catalog values as the administrator.

Each list has a **Documents** count and **Open documents** action. Open
**Tax & reporting** from Tags and use that action. Documents must open with a
removable Tag facet. Repeat from a correspondent and a document type.

The classification form explains:

- how documents match;
- which words, phrases, or pattern Paperless looks for;
- whether matching ignores letter case.

For **Any word** or **All words**, enter one word or phrase per line. Paperless
supports one matching expression per metadata value, and Odoo turns those
lines into that supported expression; it does not create a competing stack of
rules. Exact, regex, and fuzzy methods use the full expression.

Choose **Learn automatically** to use Paperless's local probabilistic
classifier. It learns from corrected, reviewed, non-inbox examples and
re-trains periodically. It does not expose a truthful list of generated
heuristics, so Odoo does not invent one or show a made-up confidence score.

Change only a harmless synthetic deterministic rule. Ingest a synthetic text
file containing that pattern in Paperless, wait for
processing/synchronization, and confirm the assigned tag appears in Odoo.

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

## 5. Attach a file from normal business work

1. Open the draft vendor bill with reference
   `USL-DOCS-CEO-QA-BILL`.
2. Add a harmless PDF or text file through the normal attachment/chatter area.
3. Open it immediately from the bill to prove the Odoo workflow does not wait
   for Paperless.
4. Its one **Documents** button briefly shows **processing**, then its archived
   count increases.
5. Click **Documents**. The workspace opens with a removable **Linked record**
   facet and the file has the Accounting/Vendor Bills context, supplier and
   document type where those defaults were available.
6. Remove the facet to search the rest of the authorized archive or link an
   already archived document.

The native attachment remains available because it is the operational Odoo
copy. Paperless keeps the archive original, OCR and archive versions. Uploading
the same bytes elsewhere reuses one Paperless root and adds another record
link.

To review failure UX without using a real business file, switch to **Needs
review** and use the seeded failed operation. It remains after reload and
offers **Choose file to retry** or **Dismiss**. A retry of the same bytes is
idempotent.

Repeat the journey from a project task, expense, TESE payroll record and
Platform Billing payout. The project file receives one project-level tag—not a
tag for every task. There is no separate **Archive in Paperless** action.

Stop Paperless briefly and attach another harmless task file. Odoo must accept
and open it; the **Documents** button shows attention or processing until the
archive returns and the retry succeeds.

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
The linked-record section also labels the exact evidence version retained by
the business relationship; replacing the current file does not move that
historical pin.

## 9. Compare shared Smart Views with Paperless

1. In Odoo, open **Configuration > Smart views**.
2. Select **New**, give the view a name, and choose the tags, document types,
   or correspondents that define it.
3. Save it and select **Open Documents**. Confirm the new shared view is
   selected and visible in the Documents sidebar.
4. Return to **Configuration > Smart views** and open **Contracts & legal**,
   **Banking**, or **Tax & reporting**.
5. Confirm it shows a stable Paperless Saved View identity.
6. In Paperless as `archive-admin/admin`, open the Saved Views management list
   and find the same three shared definitions. The Paperless sidebar shows only
   views that this Paperless user has chosen to favorite.
7. Make a harmless archive-native change, synchronize, and check Odoo again.

The Smart View configuration screen always creates shared views. Personal
searches remain private Odoo Favorites. Enabling **Available in Paperless**
also publishes the compatible archive criteria as a shared Paperless Saved
View; leaving it disabled keeps the shared navigation view in Odoo only.

The **One-click filters** on the Odoo Smart View are Odoo interaction
shortcuts, not Paperless Saved View fields. A manager can choose useful
shortcuts such as Last 30 days, Not linked, Needs review, or Group by employee.
They appear immediately before the top tag chips. Archive-native tag/type/
correspondent criteria remain synchronized with the Paperless Saved View by
stable ID; Odoo-only company, confidentiality, links, and group shortcuts are
clearly kept in Odoo.

To capture an active Documents query, choose
**Favorites > Save as one-click shortcut**, give it a name/icon, and choose
the shared Smart Views where it should appear. To review or change the whole
shortcut in one place, open **Configuration > One-click shortcuts**. The form
directly shows its Odoo filter conditions, three optional Group By levels,
three optional Sort levels, Smart View placement, icon, sequence, and active
state. **New** creates a shortcut in this same form. Add a filter condition,
choose a grouping and descending sort, save, reopen the record, and confirm
every value remains visible without opening a separate “Saved search” item.
These shortcuts do not silently rewrite a Paperless Saved View when a user
toggles one for a temporary search.

Company, confidentiality, accounting-evidence, HR, or linked-record
restrictions are labelled as Odoo policy and are not claimed to be identical
Paperless Saved Views. Shared views have no Paperless owner and are visible to
mapped identities with the Saved View read permission. A personal Paperless
view such as the seeded **Tag: Banking** belongs only to `archive-admin`;
personal Odoo views likewise remain private to their Odoo owner.

## 10. Check the Odoo-style list

1. Switch from cards to the compact list.
2. Sort Document, Date, Correspondent, Type, Company, Tags, and Status in both
   directions by clicking each header.
3. Move to another page, reload, and use Back/Forward.
4. Switch to cards and back.

The selected ordering, page, filters, grouping, and active document should
remain coherent. The list uses Odoo table conventions and Pager; there is no
second custom sort selector.

## 11. External ingestion and Trash

1. Upload a harmless file directly in Paperless.
2. Wait for processing and Odoo synchronization (normally within five minutes).
3. Find it in **Needs review**, classify it, and link it.
4. In Odoo, use **More > Move to Trash**.
5. Open Trash and confirm the detail shows who moved it and when.
6. Open its linked Odoo record, then Restore it.

The document should be **In Trash**. Its relationships remain, normal
edit/download actions are unavailable, and an authorized **Restore**
action returns the same stable identity and links.

If the move happens directly in Paperless, Odoo can display Paperless's
deletion time, but Paperless 3.0.4 does not return the deleting user through its
supported API. Odoo says that the actor was not provided rather than guessing.

Administrators see **Delete permanently** in Trash. It stays disabled with a
plain-language reason while any Odoo relationship, retention hold, or
unexpired retention window remains. Once every gate is cleared, it requires an
audit reason and leaves an Odoo tombstone. Do not permanently delete a seeded
business document during ordinary QA.

The seeded **Retention review sample — in Trash** lets you test Restore without
creating another item. Run `make documents-qa-bootstrap` afterward to return it
to the intended demo state.

## 12. Check each role

- `documents-user/admin` sees authorized general and accounting material, but
  not HR/private/other-company items.
- `documents-accountant/admin` sees the seeded accounting examples—supplier
  invoice, expense, bank statement, and VAT filing—without unrelated private or
  HR material. This role is read-only and should not see Upload controls.
- `documents-hr/admin` also sees **Camille Martin — July payroll evidence**.
- `documents-restricted/admin` gets an intentional empty archive for the
  synthetic other company and cannot infer a known title through search,
  preview, download, version, or copied URL.

Search/session state is stored per Odoo user. Switching accounts must not carry
the prior user's query into the next account.

## 13. Check an outage

The automated safe exercise is:

```bash
make documents-qa-acceptance
```

It stops only the isolated QA Paperless web service, proves an Odoo vendor bill
still opens, restores Paperless, resumes synchronization, and verifies counts
do not change. The same real-service run creates live temporary tag and
correspondent records, checks multi-term matching, tests original and processed
downloads, moves/restores a document through Trash, and removes its temporary
catalog records.

For interactive review, keep the vendor bill open while that target is running.
During the Paperless stop, Documents shows one actionable unavailable message;
the bill and the rest of Odoo remain usable. An open detail keeps its cached
classification, Odoo links, and version labels visible, hides unavailable
preview/download actions, and offers **Try again**. After recovery, retry
without creating duplicate documents or relationships.

## 14. Prove backup and restore

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
