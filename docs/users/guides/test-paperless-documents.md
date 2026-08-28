# Try the Documents application

This is a task-first guide for the isolated local QA stack. The current handoff
contains the source-complete archive plus a clearly named synthetic overlay for
safe product journeys. Use only the synthetic records and harmless test files
for changes during the tour.

## Start QA and sign in

The source-complete handoff is already deployed as Compose project
`usl-odoo-paperless-193-0824`. Open:

- Odoo: `http://odoo.localhost:19669/web/login?db=odoo_dev`
- Paperless: `http://paperless.localhost:19010`
- Pocket ID: `http://pocket-id.localhost:19411`

The governed QA personas are SSO-only:

| Purpose | Pocket/Odoo/Paperless username |
| --- | --- |
| Documents manager and HR reviewer | `documents-manager` |
| General documents user | `documents-user` |
| Read-only accounting reviewer | `documents-readonly` |
| Intentionally empty archive | `documents-restricted` |
| HR-authorized ordinary user | `documents-hr` |
| Two-company user | `documents-multi` |

Ask the operator to generate a short-lived link for the persona you are about
to test, or generate it locally without recording its output in evidence:

```bash
POCKET_ID_ENV_FILE="$PWD/.pocket-id-usl-odoo-paperless-193-0824.env" \
  scripts/pocket-id-dev one-time-link documents-manager
```

Replace the last username for another listed persona. Complete enrollment, then
choose **Pocket ID** on both login screens. Odoo and Paperless use separate
clients but the same immutable person. Pocket groups do not grant document
access; Odoo companies and Documents roles remain authoritative. Both buttons
must reach the same local Pocket tenant without a callback error.

Do not run `down --volumes`, `scripts/odoo-dev reset`, a reconstruction command,
or a bare `docker compose --profile paperless up` during the tour.

## 1. Find text inside a document

First exercise progressive exact-first search:

1. In Documents, type `INV-QA-2026-0042`.
2. Choose **Search everywhere**. The exact Alpine invoice
   must appear first, normally in under one second on the local stack.
3. While the local BGE-M3 index adds meaning-based matches, an **Exact matches
   are ready** banner is visible. The exact invoice must remain first after the
   banner disappears; semantic results may only be appended.

Then exercise meaning-only search:

1. Remove the first facet and type `heliotrope cobalt compliance evidence`.
2. Choose **Meaning (Semantic)**.
3. Confirm **Alpine Office Supplies — Invoice SI-2026-0715** is returned, then
   open it and confirm the preview contains the OCR-only sentence.

The exact marker should identify one authorized invoice; meaning-only search
may return additional authorized candidates, with the intended invoice near
the top. The preview contains the searched words. The panel immediately
explains the date, correspondent, type, tags, company, and Odoo links. Healthy
synchronization and checksum messages are absent.

Open the search dropdown. It must be Odoo's normal three-column menu:

- **Filters** includes My uploads, Ready for review, Needs attention,
  Linked/Not linked,
  Accounting, HR, availability, date ranges, and Custom Filter;
- **Group By** includes Company, Correspondent, Type, Employee, Privacy,
  Review status, and document/archive month;
- **Favorites** saves the current search for the signed-in user.

There is no separate **More filters** form.

The initial suggestions deliberately expose exactly two meaning-based choices:
**Search everywhere** and **Meaning (Semantic)**.
Title, Document content, Tags, Correspondent, Type, Company, and Date remain
lexical. Use **Filters > Add Custom Filter** for archive identity, source,
privacy, review state, availability, mapped Contact, employee, or a Paperless
custom field. Every choice remains a normal removable Odoo facet and still
passes through Odoo authorization. Search uses local BGE-M3 embeddings only;
Gemini and other generative models are not part of either search path.

To check the one-request custom-field path, use **Filters > Add Custom
Filter**, select the Paperless invoice-number field, and enter
`INV-QA-2026-0042`. It must return the same invoice without one Paperless call
per configured custom field.

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
review** and use the deliberate operation 908, `qa-corrupted-upload.pdf`. It
remains after reload and offers **Choose file to retry** or **Dismiss**. Choose
a harmless valid PDF if you test retry. Do not dismiss it merely to make the
release counter green; either outcome is an explicit reviewer decision.

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
shortcuts such as Last 30 days, Not linked, Ready for review, or Group by employee.
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
3. Find it with **Needs attention**, classify it, and link it.
4. In Odoo, use **More > Move to Trash**.
5. Open Trash and confirm the detail shows who moved it and when.
6. Open its linked Odoo record, then Restore it.

The document should be **In Trash**. Its relationships remain, normal
edit/download actions are unavailable, and an authorized **Restore**
action returns the same stable identity and links.

If the move happens directly in Paperless, Odoo can display Paperless's
deletion time, but Paperless 3.0.5 does not return the deleting user through its
supported API. Odoo says that the actor was not provided rather than guessing.

Administrators see **Delete permanently** in Trash. It stays disabled with a
plain-language reason while any Odoo relationship, retention hold, or
unexpired retention window remains. Once every gate is cleared, it requires an
audit reason and leaves an Odoo tombstone. Do not permanently delete a seeded
business document during ordinary QA.

The seeded **Retention review sample — in Trash** lets you test Restore without
creating another item. This is a mutable QA handoff, so record that manual
state change rather than running a seed or reconstruction reset afterward.

## 12. Check each role

- `documents-manager` can administer all 864 live roots available to the QA
  manager and complete reviews.
- `documents-user` sees 30 authorized general/accounting roots, but not
  HR-private or other-company items.
- `documents-readonly` sees seven accounting-evidence roots and no Upload or
  mutation controls.
- `documents-hr` sees 30 authorized roots, including HR evidence allowed by its
  company and role.
- `documents-multi` sees 30 roots across its two active companies and must lose
  company-specific results when that company is deactivated in the switcher.
- `documents-restricted` gets an intentional empty archive and cannot infer a
  known title through search, preview, download, version, or a copied URL.

Search/session state is stored per Odoo user. Switching accounts must not carry
the prior user's query into the next account.

## 13. Check Personal Gemini isolation

In Paperless as one governed QA persona, open **My profile > Personal Gemini**.
Confirm that:

1. Both optional switches start off and the page explains that the key is
   personal and encrypted.
2. There is no Gemini or archive chat UI in Odoo Documents.
3. Search, OCR, indexing and the Documents MCP continue to use the local archive
   path with no personal key.
4. If you deliberately use your own disposable test key, **Test connection**
   lists models without enabling either feature. Enable one feature, disable it
   again, delete the key, and confirm the profile returns to an unconfigured
   state.

Never paste a production or shared organization key into this QA stack.

## 14. Check an outage

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

## 15. Prove backup and restore

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
docker compose \
  --env-file .pocket-id-usl-odoo-paperless-193-0824.env \
  -p usl-odoo-paperless-193-0824 \
  -f compose.yaml -f compose.pocket-id.yaml \
  --profile paperless stop
```

This preserves QA databases, filestore, Paperless media, versions, exports,
users, and relationships.

## Report a problem

Include the task number, URL, username, active company, document title, exact
message, and a screenshot. Do not attach confidential files. Also report
anything that works technically but feels confusing, slow, or unlike normal
Odoo.
