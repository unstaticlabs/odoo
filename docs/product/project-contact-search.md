# Project and contact content search

In Projects or Contacts, type your words and choose **Content** in the search
suggestions. The existing Name, email, tag and other filters remain available.
Home uses the same content domain for its Projects and Contacts categories.

Project titles and descriptions, and contact names and notes, have private
PostgreSQL search fragments. Once refreshed, words may occur in a different
order within one source field; accents are normalized and HTML is converted to
plain text. Native link references remain searchable. Native list ordering is retained. Home ranks its own readable titles before
content-only mentions, as described in [Home result order](live-content-search.md#home-result-order).

A project title uses the current language, with the same English fallback as
Odoo when that translation is absent. A title in another language does not
contribute a match. An explicitly empty translation remains empty. Descriptions
and contact notes retain their native field permissions.

Current record rules, readable source fields and selected companies govern the
result, including fragments built before access changed. Shared contacts remain
available under their native rules. Archival filters continue to apply in the
application; Home retains its existing inclusion of archived records. An Agent
using generic MCP search can pass a `usl_search_text` domain on `project.project`
or `res.partner`, with matching language, companies and explicit archive context.
The existing abstract Home Agent access gap is outside this slice.

Native creates and source edits queue a refresh in their transaction. Each fixed
scheduled action processes at most 500 tasks, projects or contacts per minute.
Pending records exclude old fragments and retain live contains matching. Word
matching becomes available after refresh; a backlog or failed run can extend
that delay. Searches never refresh the projection.

Existing fields, properties and related-record labels continue through the live
search implementation. This slice therefore does not establish a global speed
target or replace every live predicate. A reordered-word result can currently
have an empty Home excerpt. Source explanations, richer sources, semantic matching and graph traversal
remain separate work.

## Maintenance and upgrade

Tasks, projects and contacts share PostgreSQL mechanics through a private,
Python-inherited Odoo mixin. Their separate tables retain native foreign keys. The mixin has no registry model, table or public helper.
Literal mutation targets remain visible to the action inventory. Existing task
projection table names, schema and content are retained. Project fragments additionally distinguish title languages.

Normal upgrades queue missing projections and keep ready results available.
Installation or reinstallation queues the relevant source set again. For an
explicit repair in an operator's Odoo shell, projects and contacts expose the
private `_usl_queue_content` helper; `all_records=True` requeues that model's full
source set. Tasks keep their existing `_usl_queue_task_content` helper. After
queueing, allow the fixed scheduled actions to refresh. Direct SQL repairs to
source text must explicitly requeue the affected records.

Publication and queue removal are one transaction. Queue upserts wait for a
worker holding the row; Odoo's native request retry handles a concurrent
serialization failure. A failed refresh retains the batch for another attempt.
The refresh writes only derived projection tables. Original text, translations,
messages and attachments remain the source of truth.

See [task search](task-search.md) and
[the PostgreSQL projection decision](decisions/0004-postgresql-task-search-projection.md).
