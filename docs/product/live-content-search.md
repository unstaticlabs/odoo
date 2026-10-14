# Find work by its contents

Home has a live search above its attention cards. People can find a task from a
sentence in its description, a custom property, or an activity note, without
remembering its title. Results show the record, accessible project/company
context, and a short matching excerpt. Opening a result uses its native form;
Back returns to the Home query.

The native Tasks search field uses the same content search. Existing Open,
My Tasks, project, company, Group By and saved-filter restrictions still apply.
On Projects, the existing search bar also offers direct matching tasks and
projects across accessible projects. Its ordinary project filters remain
available alongside these destinations.

## What is searched

Tasks include readable text, HTML descriptions, native custom properties,
selection labels, exact numeric/date values, accessible related record labels,
and related activities. Property schemas follow native Properties authority:
a readable task can supply its schema even when its Project is private. This
metadata boundary does not grant access to the private Project's name or other
business fields. Each property predicate remains bound to its own schema owner.

Home groups matches by the installed business app: Tasks, Projects, Documents,
invoices and entries, accounting lines, expenses, contacts, sales orders,
purchase orders, opportunities, bank transactions, products and inventory
transfers. A category appears only when the caller can read its model. Local
business search uses current native values; it has no duplicated index or cron
refresh interval. Ordinary task descriptions remain searchable when they
discuss credentials, while credential fields/properties and binary/signature
values are excluded.

Documents reuse the existing scoped content-search service. It can combine
exact OCR/content matches with meaning-based matches when its semantic engine
is available. This does not add embeddings or fuzzy search to Tasks or Projects.
Local results load independently of the document provider. A document outage
has its own retry; exact document matches remain usable when only the semantic
engine is unavailable. Home offers a bounded document preview window, then a
native Documents destination carrying the query.

## Access and navigation

Native model, field and record access applies to every source. Related business
labels are searched through the related model's own authorized query. Assignee
names use native User read/search authority, including readable public identities
whose default company differs. The raw-SQL portal-name helper is excluded from
this new generic expansion because it bypasses related User rules; existing
native portal search behavior is unchanged. A permitted task description remains
searchable even if it contains the same text as a denied related identity. Selected
companies constrain matches and destinations; shared records remain eligible
under their native rules. Results recheck current access when opened. They do
not carry stale project defaults into an unrelated task form.

Typing, Refresh and company changes request current values. Clearing the query
invalidates pending responses. Pagination does not require loading every match
at once. Keyboard users can select a Home result with the arrow keys and open
it with Enter; native Projects suggestion navigation remains available. The
search input does not automatically claim focus on mobile.

The existing command palette and its first-suggested Feedback command retain
their own native shortcut and behavior.
