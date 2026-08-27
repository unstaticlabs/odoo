# Restore Odoo Online Sign records

The implementation and exact artifact disposition are documented in
`migration/sign_restore/README.md`. This runbook records the operational order.

1. Restore and validate identities first. Every source requester and signer
   must resolve to exactly one target user or contact by temporary source
   binding, with exact email as the guarded fallback.
2. Keep the source database and filestore read-only. Place the sixteen exported
   PDFs under `<dump>/sign` as eight signed-document / `Certificate - …` pairs.
3. Run `scripts/sign-restore prepare-target`, then `scripts/sign-restore all`
   against an isolated reconstruction Compose project and target database.
   A focused rehearsal must use a disposable database. The canonical
   reconstruction calls the same runner with `SIGN_CANONICAL_TARGET=1` for its
   isolated `odoo_dev`; that explicit mode is not accepted for another database.
   Never point the runner at a QA project.
4. Retain the private import and validation logs. Confirm eight external
   requests, eleven external participant rows, eighty-six preserved history
   messages, exact PDF/certificate checksums, five linked Paperless artifact
   purposes per request, synchronized permissions and an identical replay.
5. Finalization must remove every `usl_sign_restore` binding while leaving the
   eight requests and eleven participant rows unchanged. Run
   `make product-migration-boundary` as part of the wider reconstruction.

Never enable `USL_EINVOICE_LIVE_ENABLED` or `USL_EREPORTING_LIVE_ENABLED` for
this work. The Sign restore performs no live signature, passkey, certificate,
timestamp, trust-list, revocation, invoice or e-reporting action.
