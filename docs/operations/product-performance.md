# Product performance audit

## Native Ollama and embedding batches — 29 August 2026

The final Online reconstruction initially reached BGE-M3 through Docker
Desktop's Linux VM. That runtime had no Apple Metal device and saturated 16 CPU
threads. The macOS-native Ollama 0.33.1 runtime detected the Apple M4 Max,
offloaded all 25 BGE-M3 layers to Metal, and completed the governed semantic
update for 1,056 documents and 8,467 vectors in 252 seconds. Release inventory
reported zero active or historical failed tasks and exact document/vector
coverage.

The Ollama `/api/embed` endpoint accepts an input array, but Ollama deliberately
loads embedding models with parallelism one. A representative benchmark used
128 real 512-token Paperless chunks, two timed repetitions per batch size, and
compared the first eight batched vectors against separately embedded vectors.
Throughput rose from 23.0 chunks/s at batch 1 to 42.3 at batch 10, reached 43.8
at batch 32, and then plateaued at 44.1–44.3 through batches 48–128. Every
tested vector was bit-identical to its single-item result. Batch 32 is selected:
it captures nearly all measured throughput with a 0.73-second median request,
while batches 48–128 add response size and latency for less than 2% additional
throughput.

Local macOS Documents and migration wrappers now select native Ollama whenever
it is installed and reachable, verify the exact BGE-M3 manifest, and fail rather
than silently falling back to a CPU container. Linux production and recovery
retain the pinned container and portable model volume.

## Integrated performance pass — 27 August 2026

The combined Documents candidate exposed two additional measured bottlenecks.
The first was product-facing: opening a 40-document workspace, including its
authorized record facets, used 426 SQL queries and 432.8 ms in an isolated
fresh-install database. The second was migration-facing: the source-complete
archive rehearsal interleaved every consumed document with a separate BGE-M3
Celery job. Individual semantic jobs took approximately 55–337 seconds on the
local CPU-saturated Ollama service, leaving 15 uploads waiting behind the
embedding worker for most of the run.

The integrated fixes are deliberately separate:

- Workspace links, record facets, Contacts, Employees and computed presentation
  roles are resolved in batches. The same 40-document request now uses 75
  queries and 67.3 ms: 82% fewer queries and 84% less local server time. The
  regression ceiling is 85 queries, and all target visibility checks still run
  through the caller's Odoo ACLs and record rules.
- Processing/failure badges on 40 business records now use one grouped
  operation read instead of 80 independent `search_count` calls. The complete
  read uses 10 queries, and active operation lookup has a partial composite
  index on record identity and state.
- Governed source reconstruction now defers only incremental semantic-index
  signals while the bounded upload/OCR/metadata phase runs. It then force
  restores normal Paperless behavior and executes the supported
  `document_llmindex migrate`, `update` and `compact` sequence once. Release
  inventory must prove zero active tasks and exact document/vector coverage;
  failure restores the normal runtime and fails the migration. Ordinary user
  uploads are unchanged, and production admission rejects the deferral switch.

Paperless `3.0.5-usl.7` contains the controlled bulk
index path. Its hash-guarded ARM64 image built successfully and its final 22
focused Django tests passed. The optimized locked-source reconstruction reused
646 exact governed roots without re-uploading bytes, archived 832 eligible
native attachments, and finalized 1,148 live documents plus nine Trash
records. The semantic gate indexed all 1,148 live documents into 8,654 chunks,
with zero active tasks. Production AMD64 qualification remains a separate
release-host gate.

The final pass also made the restore itself genuinely idempotent. Exact reuse
now requires content, governed metadata and company identity rather than a
checksum alone; unchanged archive metadata and object permissions perform no
remote writes; native attachments are settled before semantic finalization;
and the temporary migration service identity is separated from the permanent
runtime identity. A worker-side deferral guard also makes already-queued
embedding jobs harmless during governed bulk reconstruction. Repeated identity
synchronization consequently changed zero Documents or permissions.

### Wider audit findings

Existing QA evidence shows why the reusable seed remains important. Clean
product installation measured 274 seconds. Qualified seed hydration measured
88–127 seconds, while branch-specific upgrade/finalization measured 199–353
seconds depending on the integrated module set. The current cache design keeps
writable databases and service volumes isolated, validates content digests,
and records zero OCR/download work on a warm hit; weakening those checks for a
faster apparent result was rejected.

The Odoo image build sent about 1.08 GB of required upstream source in 26
seconds during an uncached context transfer. Most of that is the delivered
Odoo source tree, not disposable artifacts; dumps, Git data and private
migration evidence are already excluded. A split prebuilt upstream-source base
could reduce developer rebuild latency, but it would add another release
artifact and identity boundary. It remains a measured follow-up rather than an
unreviewed change in this pass.

Increasing Paperless workers was also rejected as the primary migration fix.
Ollama was already saturating the available CPU, so extra Celery workers would
mainly increase memory pressure and contention. Increasing embedding chunk size
would reduce work but alter retrieval quality and the frozen release contract.
The selected two-phase flow preserves the qualified BGE-M3 model, 512-token
chunks, OCR output and final vector parity.

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
document-badge queries, 12 operation-status queries, 25 link-helper queries,
10 metadata-resolution queries or 85 complete-workspace queries. Run
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
