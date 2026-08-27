# Product performance audit

## Scope and method

The 2026-08-27 audit covered the delivered custom add-ons, the Odoo runtime
configuration, generated backend assets, scheduled Documents work, and idle
container resources. Measurements used the isolated Compose project
`usl-odoo-qa-bd5efa49`, dedicated ports, and the disposable
`odoo_product_perf` database. Regulatory live flags remained disabled.

The code audit searched every custom add-on for computed fields, unbounded
searches, per-record searches, scheduled actions, and globally loaded frontend
assets. Controlled fixtures then exercised 40 records, deliberately exceeding
one ordinary Odoo list page. Query counts use Odoo's cursor counter after a
cache invalidation; timings are indicative local measurements, while query
ceilings are the durable regression contract.

## Measured results

| Path | Before | After | Result |
| --- | ---: | ---: | ---: |
| 40 business-record document badges | 50 queries, 42.5 ms | 13 queries, 17.3 ms | 74% fewer queries |
| 40 document link helper reads | 137 queries, 48.8 ms | 20 queries, 20.1 ms | 85% fewer queries |
| 40 Paperless metadata resolutions | 122 queries, 21.3 ms | 6 queries, 1.5 ms | 95% fewer queries |
| eager backend JavaScript, minified | 7,615,925 bytes | 7,492,202 bytes | 123,723 bytes removed |
| eager backend JavaScript, gzip | 1,608,277 bytes | 1,583,354 bytes | 24,923 bytes removed |

The Documents workspace is now a separate 125,402-byte minified bundle
(25,632 bytes gzip). It is generated and cached as an immutable asset, but is
not downloaded or parsed until a user opens Documents. The small record smart
button and action loader remain in the eager backend bundle.

An idle clean-install stack measured Odoo at 408 MiB, PostgreSQL at 226 MiB,
and the separate Paperless processing services at approximately 954 MiB in
aggregate (with Pocket ID adding another 16 MiB). This makes two ownership
boundaries clear: ORM and asset work
belongs in Odoo, while OCR/Tika/Paperless capacity must be tuned as a separate
Documents service concern.

## Implemented controls

- Document badges gather relationship candidates in batches, then intersect
  them with the caller's readable Documents. Access-dependent counts remain
  non-stored and record rules remain authoritative.
- Link visibility performs one target query per linked model instead of one
  access check per relationship. Search helpers and local text search reuse the
  batched result.
- Active document-link lookup has a partial composite index on business model,
  record ID, and document ID.
- Paperless synchronization preloads three metadata catalogs once and fetches
  existing Documents once per remote page. Trashed-page lookups are batched in
  the same way.
- Production-like Odoo services use explicit connection, memory, and request
  recycling budgets. Production admission rejects missing, inconsistent, or
  overcommitted values.

The production example uses four HTTP workers, one admitted cron worker, a
12-connection pool per process, 1 GiB soft and 1.25 GiB hard memory limits, and
an 8,192-request recycle interval. Including HTTP, cron, and evented workers,
its theoretical application pool is 72 connections, leaving headroom below
PostgreSQL's normal 100-connection default. Pre-production uses two HTTP
workers and a 20-connection pool, capped at 80 connections including cron and
evented work.

## Alternatives considered

Storing the badge counts would make reads cheap but would be incorrect: the
value changes with company, document confidentiality, and target-record access.
The selected batched, non-stored computation preserves those rules.

Direct SQL could reduce a few more ORM calls, but would duplicate record-rule
logic and create a security maintenance burden. Candidate relationships are
therefore collected with narrow sudo searches and filtered through a normal
`usl.document` search before they affect user-visible results.

Increasing cron intervals would reduce wake-ups but would also increase upload
and synchronization latency. The one-minute operation poll is an indexed,
bounded empty query; the expensive work was the per-document metadata and
existence lookup, so that work was batched without changing product freshness.

Container-only memory limits were rejected as the primary application guard:
an OOM kill interrupts work abruptly. Odoo's native soft limit, hard headroom,
and request-count recycling retire workers cleanly. Container capacity limits
may still be added by the deployment platform as a second boundary.

Global PostgreSQL memory changes were not made without production working-set,
host RAM, and cache-hit evidence. The audit adds the missing application pool
budget first; database tuning should follow observed production statistics.

## Regression and operational checks

The `usl_documents_performance` tests fail if the 40-record paths exceed 15
badge queries, 25 link-helper queries, or 10 metadata-resolution queries. Run
them in an isolated test database with:

```bash
docker compose -p "$COMPOSE_PROJECT_NAME" --env-file "$QA_ENV" --profile test run --rm test \
  odoo --config=/etc/odoo/odoo.conf --database=odoo_product_perf \
  --update=usl_documents --without-demo=true --test-enable \
  --test-tags=usl_documents_performance --stop-after-init --log-level=test
```

Before production admission, confirm the rendered Odoo configuration contains
the approved `workers`, `db_maxconn`, `limit_memory_soft`,
`limit_memory_hard`, and `limit_request` values. Monitor worker recycle logs,
request latency, cron duration, PostgreSQL connection occupancy, and document
sync backlog after release; adjust only from observed evidence.
