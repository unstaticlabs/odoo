# Product performance

This document records durable performance decisions and regression ceilings
for the running Distribution. Temporary reconstruction timings and obsolete
environment comparisons do not belong here.

## Documents and Paperless

Opening a 40-document workspace must use no more than 85 Odoo SQL queries. The
qualified implementation used 75 queries and approximately 67 ms of local
server time, down from 426 queries and 433 ms before batching.

The product maintains this result by:

- resolving links, record facets, Contacts, Employees and presentation roles
  in batches;
- grouping active-operation counts instead of issuing per-record counts;
- indexing active document links by business model, record ID and state;
- filtering user-visible results through Odoo ACLs and record rules;
- loading the Documents workspace as a separate lazy asset bundle.

Counts that depend on company or record access remain non-stored. Direct SQL
must not replace record-rule evaluation merely to reduce query counts.

## Embeddings

Local macOS development and protected local operation use native Ollama when
it is installed, reachable and exposes the exact qualified BGE-M3 model. The
runtime fails closed instead of silently falling back to a CPU-only Docker
container.

Linux production and recovery use the pinned containerized Ollama image and
portable model volume. The release cohort records the exact model identity,
chunking contract and vector dimension.

The qualified local batch size is 32. A benchmark on 128 real 512-token chunks
measured approximately 43.8 chunks per second at batch 32, with bit-identical
vectors versus single-item requests. Larger batches added latency and response
size for less than two percent more throughput.

## Runtime capacity

Production-like Odoo services use explicit connection, memory and request
recycling budgets. The reference configuration uses four HTTP workers, one
admitted cron worker, a 12-connection pool per process, 1 GiB soft and 1.25 GiB
hard memory limits, and an 8,192-request recycle interval. The complete
application pool must remain below PostgreSQL's connection limit with operator
headroom.

Paperless OCR, Tika, search and embedding capacity are separate from Odoo ORM
capacity. Adding workers is valid only after measuring the actual bottleneck;
it must not increase contention on Ollama or PostgreSQL.

## Release checks

For changes that affect these paths:

1. run the focused Odoo or Paperless suites;
2. measure the 40-record query ceilings with access rules enabled;
3. verify the exact embedding model, dimension and representative vector
   identity;
4. render the production Compose configuration and check worker, connection
   and memory budgets;
5. confirm that ordinary uploads, OCR, search and vector updates remain
   enabled and that no bulk-operation deferral flag can enter production.

Performance work may not weaken access control, evidence completeness,
duplicate protection, release identity or recovery guarantees.
