# First production deployment incident review

## Outcome

The first migrated production deployment completed, but the end-to-end effort
took more than 16 hours. That duration is not the operating baseline. The first
run mixed one-off migration transfer, infrastructure discovery, application
repairs, image publication, production configuration, and recovery-tool
development. Several failures were found sequentially and triggered repeated
build or rollout work.

The replacement continuous-operations path has since restored a complete
production cohort into fresh staging volumes in 332.411 seconds. It reused OCR,
previews, Tantivy, and vectors and passed the recorded business controls without
human intervention.

## Why the first run took so long

1. **The release cohort was assembled during deployment.** Image identities,
   MCP and renderer compatibility, Compose topology, secrets, ingress, and the
   shared Ollama contract were not resolved once before the outage.
2. **Build contexts were too broad.** Unrelated repository changes invalidated
   expensive Docker layers. Images were rebuilt one after another instead of
   independently resolving content-addressed artifacts.
3. **Recovery wrappers overlapped.** Historical migration, candidate, cutover,
   Paperless, and backup entry points inferred different projects, paths, and
   environment variables. Safe fail-closed checks then required manual
   reconciliation.
4. **Qualification happened incrementally.** Production configuration issues
   were found after earlier phases had already run. That created repeated
   deploy-check-fix cycles instead of one preflight and one rollout.
5. **Large derived document state was treated like primary data.** OCR,
   previews, search, and embeddings were copied through slow paths before the
   reusable cache contract was established.
6. **The VPS had limited working space.** Image layers, temporary restore data,
   old release evidence, and rollback archives competed for disk, requiring
   cleanup during the operation.
7. **The first deployment included external setup.** Pocket ID, Cloudflare,
   Resend, Gmail aliases, PDP registration, shared Ollama, Paperless, Sign, and
   MCP were production integrations, not merely a database restore.

## Corrections

- `scripts/usl-stack` is the only continuous backup, restore, health, smoke,
  and generation-cleanup interface.
- Versioned target files resolve runtime identity, secrets, resources, ingress,
  databases, and Ollama once.
- CI builds components independently and reuses immutable content tags and
  registry cache. Release assembly binds exact digests.
- Coordinated cohorts separate durable records from reusable OCR, preview,
  Tantivy, and vector cache while verifying both.
- Restore pre-pulls images, uses fresh labeled volumes, emits phase timings,
  checks capacity, atomically activates, validates, and rolls back on failure.
- Production and staging have explicit CPU, memory, PID, swap, and OOM-priority
  policies. Staging yields to production.
- Cleanup protects the active generation and one rollback generation and uses
  exact Docker ownership labels.

## Normal expectations

- A fully cached release workflow should finish in a few minutes.
- A changed component rebuild should affect only that component.
- The measured fresh staging restore baseline is 5 minutes 32 seconds.
- Backup plus independent restore must remain below 30 minutes unattended.
- A failed activation must leave the prior runtime running and produce a short
  failure and rollback summary.

## Remaining external status

French PDP/Peppol reception may remain enabled while Odoo reports registration
pending. Sending and e-reporting remain gated until the provider accepts the
production registration. This external wait does not invalidate backup,
restore, staging, or release readiness.
