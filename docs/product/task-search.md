# Task content search

The native Tasks content filter and the Tasks category on Home use the same
`usl_search_text` domain. Readable task titles and descriptions have a private
PostgreSQL lexical projection. After indexing, words may appear in a different
order, accents are normalized, and descriptions are searched as plain text.
Substring matches are retained, including incomplete words. A multiword lexical
match must occur within one source field. Native list ordering is unchanged.

On databases with Odoo's native accent dictionary, a search for `coeur` also
finds `cœur` before and after indexing. Long descriptions remain searchable
from beginning to end, including when the search terms are far apart.

The title and description of each changed task are queued transactionally. The
scheduled action **Tasks: refresh search index** processes up to 500 tasks each
minute. Pending tasks remain searchable through live native contains matching;
their old indexed text is excluded. During an initial backfill or sustained
backlog, word-based matching may take more than one minute to become available.
A failed index transaction retains its queue entries for a later retry.

Current field permissions, native task record rules and selected companies govern
results even when a fragment was built before access changed. Indexing does not
grant source access. Searching does not create or update a projection.

Other readable values, properties, linked-record labels and activities keep their
existing live search behavior. Home still presents results by category and native
ordering; a reordered-word match can have an empty excerpt until source-aware
excerpts are added. Documents retain their separate existing search engine.

Installation and module upgrade queue tasks with missing projections for
backfill; a repeat upgrade keeps already indexed results available. Installation/reinstallation explicitly requeues all tasks, including retained
fragments from an earlier installation. A repair can requeue a task through the
private `_usl_queue_task_content` maintenance helper in an operator's Odoo shell,
then allow the fixed scheduled action to process it.
For a full repair, the same private helper accepts `all_tasks=True` on the model.
The projection tables are derived data and can be rebuilt; original task rows,
messages and attachments are not rewritten by indexing.

See [the architecture decision](decisions/0004-postgresql-task-search-projection.md)
for concurrency, authority and the boundaries of this first slice.

Agents can use the generic MCP `odoo_search_records` tool with `fields` and a
`usl_search_text` domain on `project.task`, scoped to the same selected companies.
This uses the native `search_read` path. Direct Agent read-only access to the
abstract Home service is not currently approved. The optional `specification`
path uses `web_search_read`, currently classified as recoverable and refused to
this read-only profile; it is not the qualified MCP path for this slice. Precise
Home/MCP parity belongs to the later integration slice, without a general grant.

A direct SQL repair of indexed source fields must requeue the affected tasks;
ordinary ORM edits already do this transactionally.
