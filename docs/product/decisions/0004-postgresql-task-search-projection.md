# 4. Keep the first task search projection in PostgreSQL

Date: 2026-11-26
Status: accepted

## Context

Task content search combines live predicates over readable values, properties,
related labels and activities. This preserves native authority but offers no
word-based matching and repeatedly reads task descriptions. The existing native
search field also feeds the Tasks category on Home.

## Decision

Project task titles and descriptions into private PostgreSQL fragments, separated
by source field. Maintain a GIN full-text index using the `simple` configuration
and a substring trigram index when PostgreSQL provides Odoo's native extension.
Normalize whitespace, accents and HTML description text before indexing. A
word-based match requires every query lexeme in the same source field; substring
matching remains available. Search order remains the caller's native order.

Create and relevant writes enqueue the task in their own transaction. A fixed
one-minute scheduled action consumes at most 500 tasks using `FOR UPDATE SKIP
LOCKED`. Queue upserts must update the conflicting row, so they wait for an
indexer holding that row; `DO NOTHING` could lose a concurrent invalidation.
Publishing fragments and removing queue entries is one transaction. Odoo uses
repeatable-read transactions; a conflicting writer can receive a serialization
failure and is retried by the native request boundary. Qualification exercises
that actual retry path, rather than changing isolation or bypassing the lock.

A queued task cannot match an old fragment. It retains live native contains
matching until refreshed. A query never writes the index. The module's `init`
queues tasks with missing projections on installation or upgrade, allowing a
background backfill without changing source business records. A repeat upgrade
keeps existing ready results available. Installation/reinstallation explicitly
requeues all tasks because source writes may have occurred while the add-on was
uninstalled. Explicit repair uses the private queue helper; future changes to
the projection format need their own requeue migration. Foreign keys remove the
derived rows when their task is deleted.

The projection is not a public Odoo model. Native task queries apply the caller's
current record rules and selected companies to the combined domain. Before
consulting fragments, search limits source fields to those the caller can
currently read. Maintenance reads fixed source columns privately; it never
returns their content as an authorization result.

## Consequences

Existing title/description substring searches remain available during backfill.
Word-order-independent and markup-free matching takes effect after the source is
indexed. There is no freshness claim of one minute while a backlog exceeds one
batch or a cron run fails. Other fields, properties, related records and activity
search retain their current implementation and cost. Existing Home excerpts may
be empty for a reordered-word match because excerpt selection still uses the
literal query; source explanations belong to the next source/UX slice.

This first slice does not provide global relevance ordering, typo correction,
chatter/transcript indexing, graph ranking, embeddings or a new MCP contract.
It changes no native business authority or state. The projection and queue are
derived, rebuildable data; they are not an audit log or a new source of truth.

## Alternatives considered

- Add only trigram indexes to live fields. This preserves immediate substring
  semantics, but does not create a clean text projection or word-based matching
  and retains repeated broad live predicates.
- Add an external search service. It offers a richer search engine, but adds
  operational state, synchronization and another access-control boundary before
  the first use case is qualified.
- Refresh projections while searching. This reduces visible indexing lag but
  makes ordinary read-only searches mutate state and couples response latency to
  rebuilding, so it is excluded.
