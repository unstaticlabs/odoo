# Find work by its contents

Home has a live search above its attention cards. People can find a task from a
sentence in its description, a custom property, or an activity note, without
remembering its title. Results show the record, accessible project/company
context, and a short matching excerpt. Opening a result uses its native form;
Native Odoo Back and ordinary browser history Back return to the Home query.
Only the query is restored; results are requested again under current access.

The native Tasks search field uses the same content search. Existing Open,
My Tasks, project, company, Group By and saved-filter restrictions still apply.
On Projects, the existing search bar also offers direct matching tasks and
projects across accessible projects. Its ordinary project filters remain
available alongside these destinations.

## What is searched

Tasks include readable text, HTML descriptions, native custom properties,
selection labels, exact numeric/date values, accessible related record labels,
and related activities. Readable task conversations also provide message sources;
see [task conversation search](task-chatter-search.md) for eligibility, refresh
and native source opening. Property schemas follow native Properties authority:
a readable task can supply its schema even when its Project is private. This
metadata boundary does not grant access to the private Project's name or other
business fields. Each property predicate remains bound to its own schema owner.

Home groups matches by the installed business app: Tasks, Projects, Documents,
invoices and entries, accounting lines, expenses, contacts, sales orders,
purchase orders, opportunities, bank transactions, products and inventory
transfers. A category appears only when the caller can read its model. Local
business search uses current native values. Tasks, projects and contacts also
use rebuildable PostgreSQL lexical projections; their documented refresh and
pending-source behavior apply. Ordinary task descriptions remain searchable when they
discuss credentials, while credential fields/properties and binary/signature
values are excluded.

Documents reuse the existing scoped content-search service. It can combine
exact OCR/content matches with meaning-based matches when its semantic engine
is available. This does not add embeddings or fuzzy search to Tasks or Projects.
Task and Project results load independently of the remaining business categories
and the document provider. Available results stay usable while another batch is
still searching. A category failure preserves results already found and offers
a retry. A document outage
has its own retry; exact document matches remain usable when only the semantic
engine is unavailable. Home offers a bounded document preview window, then a
native Documents destination carrying the query.

## Home result order

Within Home’s Tasks, Projects and Contacts categories, a readable own title equal
to the query comes first, then a title containing the query phrase, then a title
containing all its lexical words. Other authorized content matches follow.
Case and accents use the installed native PostgreSQL normalization. The title
uses the current language and native fallback, rather than an inaccessible
translation or related record label. Descending record ID breaks every tie.
The database applies this order before each page window; unchanged data and
permissions produce the same concatenated order at any supported page size.
The existing category display priority also stays unchanged: a task conversation
can precede the Projects group even when a project has an exact title. Ranking
is within each category, rather than a global score across business apps.
New records, edits or permission changes between requests can naturally change
the result set, so offset pagination is not a historical snapshot.

The search domain and category/exclusion filters are unchanged. Ranking never
reads extra chatter, related identities or hidden title fields. Existing source
selection and the destination access recheck remain in place. The native Tasks,
Projects and Contacts lists keep their own sort choices. Other Home categories
and Documents retain their existing engines and ordering.

This ranking does not correct spelling: `Attelier Ambre` does not find a record
containing only `Atelier Ambre`. The existing trigram index speeds substring
predicates; fuzzy matching would need an explicit retrieval contract and its own
quality and access qualification. The annotated synthetic FR/EN corpus is in
`custom-addons/usl_home/tests/data/search_relevance.json`.

Agents retain the existing concrete-model MCP search path. On `project.task`,
`project.project` or `res.partner`, pass the `usl_search_text` content domain,
`order: "usl_search_text"` and `context: {search_relevance_query: query}` with
the same text. At the same identity, selected companies, language and archive
context, this explicit order uses Home’s title ranking and ID tie breaker.
Use `active_test: false` to match Home’s archive scope. The native field help
advertises this option on supporting releases; older releases reject ordering
by this virtual field and must not be presented as relevance-equivalent.

The existing `odoo_search_records` tool forwards that native order and uses
offset cursors for it. Ordinary ID, name or date orders remain unchanged,
including ID-only keyset cursors. Task source explanations additionally require
`search_source_query` and the readable `usl_search_sources` field. No new MCP
tool or abstract Home service delegation is introduced. Native model/field
rights, companies and source revocation apply before ordering. The shared
compiler reads only the current readable own title; a hidden title falls back
to descending ID. The explicit relevance query must be non-empty and at most
2,048 characters. This ordering does not expand the matching domain or apply
fuzzy correction.

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
aborts superseded browser requests as well as invalidating late responses. At
most two local search batches and one document request belong to the current
Home query; Projects has one task lookup and one project lookup. Aborting the
browser request does not promise cancellation of a query already executing on
the server. Pagination does not require loading every match
at once. Keyboard users can select a Home result with the arrow keys and open
it with Enter; native Projects suggestion navigation remains available. The
search input does not automatically claim focus on mobile.

The existing command palette and its first-suggested Feedback command retain
their own native shortcut and behavior.


## Home route and upgrade boundary

Home uses the native action path `/odoo/usl-home`. Its action ID, menu, component
tag and users’ configured default action remain unchanged. The unique path avoids
Odoo’s built-in `home` command, which redirects and reloads the page. Old
`/odoo/home` bookmarks retain native default-home fallback behavior; an existing
pre-upgrade history entry does not gain query recovery retroactively.

The query uses Odoo’s hidden history state and does not enter the URL. An explicit
clear stays empty across Forward and Back. Ordinary result navigation and native
breadcrumbs are qualified separately. Explicit hard reload or leaving the app for
another document is a separate native startup boundary; no custom persistent
storage is introduced here.

## Query and memory bounds

Task pages keep authorized activity matching inside the database rather than
loading every matching activity ID into application memory before applying the
task page limit. Repeated property predicates are combined only when their
expressions are identical, and remain constrained to the exact readable schema
owners. Different selection meanings, field permissions and selected companies
retain their own boundaries. This adds no cross-user cache or persistent index.

Search still evaluates live readable fields and discovers the readable property
schemas. Distinct schemas and large related-label populations still have a cost;
this change does not impose a result cutoff that silently drops matches. The
regressions measure native database fetch cardinality and statement growth, plus
partial results and cancellation through the native browser request API.

Task content searches also avoid PostgreSQL JIT compilation while their native
page, count, grouped or Pivot query executes. Broad readable-field predicates can spend
more time and memory compiling than executing. The setting is local to that
execution and restored afterward; ordinary Task searches keep their existing
planner settings. This does not change matching, ordering, page sizes or counts.
Database errors retain the caller's normal rollback requirements.

## Tasks about the same subject

When a query has at least two words, or one word of eight characters or more, Home also
looks for tasks about the same subject, in French or English, at the same time as the
keyword search. Keyword matches stay where they are. Tasks found only by meaning are
appended to the Tasks group under **Related by subject**, each with a quiet
**Similar topic** badge. Without any keyword task the group reads **Tasks about this**.
A skeleton row stands in for them while the model answers. If the model is slow
(1.5 s in all) or unavailable nothing is said: the keyword rows are already there.
The empty message appears only after every stream has finished. A suggestion opens the
original task. The same read is available through
`project.task.search_by_meaning(query, limit=5)` for delegated callers; its status is
`ok`, `empty`, `unavailable` (the model did not answer) or `disabled` (the deployment
does not offer the search, so Home stops asking).

The feature is off by default. On a database with the `vector` extension already
installed, upgrade `usl_home`, then enable `USL_TASK_SEMANTIC_SEARCH=1` with the
same Ollama endpoint, model and manifest digest used by Documents. The service
checks the model identity before sending text, and remembers that check for five
minutes in each worker, so a search makes one HTTP call. The background job indexes
the first 2,048 characters of the title and the original description, up to 16 tasks
each minute; a task with only a title is found too. The vector sits on the task's
title fragment. Edits immediately remove stale suggestions until the task is indexed
again. Changing the model starts a fresh pass. The reader's current task and field
access governs each result. No messages, files or transcripts enter this index.
