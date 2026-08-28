# Historical agent prompt migration

The five supplied prompts were audit inputs, not durable instructions. Their
useful decisions now live in the following canonical locations; the prompt
files should not be pasted or maintained as a parallel workflow.

| Historical prompt | Durable destination | Superseded or narrowed material |
| --- | --- | --- |
| `closing-feat-branch.md` | `AGENTS.md` role/resource invariants; `usl-feature-developer` closeout; v1 handoff fields; `verify feature-ready`; QA cleanup ownership; operating guide | Free-form prose appended to a later prompt is replaced by review-first generated PR Markdown backed by a collapsed machine-readable contract. Feature Developers preserve review resources and never archive, merge or delete the worktree. Cleanup is ownership-checked, not an open-ended Docker sweep. |
| `Feat Agent Pull CatchUp (Prompt).md` | Feature skill catch-up procedure; context and branch verification; changed-path/module facts in context/handoff; specialist requalification | Unconditional clean bootstrap, full Online restore, full migration rerun and every broad regression suite are obsolete as generic requirements. Run them only when the delta/risk requires them. Future rollback is from canonical Community backups, not Online. |
| `qa-env.md` | `qa-up`, `qa-status`, `qa-down`; versioned QA status schema; Feature/Lead skills; QA section of the guide | A manual one-off deployment description is replaced by stable commands. V1 deliberately reports remote HTTPS unsupported; it does not invent a tunnel or reuse production OIDC secrets. Functional QA remains an explicit evidence task, separate from merely provisioning an environment. |
| `lead-merge-feat.md` | `usl-lead-developer`; migration, accounting, access-control and UI Skills; handoff contract; Lead start/readiness checks; transition activation list | The prompt's integration ownership, independent review, merge ancestry, overlap cleanup and post-merge resource rules remain. Its migration-project assumptions—always restore Online, always rerun the entire one-off migration, mutate `odoo_dev` for every merge, archive every feature tip—are not permanent release policy. Normal integration happens by reviewed PR; GitHub/CI enforcement activates after cutover. |
| `sync-upstream.md` | `usl-upstream-sync`; Feature handoff/PR flow; migration qualification | Upstream ancestry, dedicated sync branch, semantic conflicts and separated USL adaptations remain. Copy/paste reporting becomes the normal contract; the sync agent still stops before integration. Hardcoded dates and unconditional broad runtime suites are replaced by exact context and risk-based validation. |

## Cross-cutting disposition

- Permanent invariants live only in `AGENTS.md`: development branch, isolated
  worktrees, PR/role separation, shared-resource safety, migration/recovery
  evidence, destructive-operation care and CI-owned production deployment.
- Role procedures live in the Feature, Lead and upstream-sync Skills.
- Reusable risk qualification lives in four specialist Skills. Release
  readiness stays with the Lead rather than becoming a duplicative eighth
  Skill. Repository context stays a deterministic script.
- Deterministic naming, repository facts, schema validation, readiness, identity
  isolation, publication and QA ownership are scripts/contracts rather than
  prose the agent must reconstruct.
- Durable product and operations facts remain in existing `docs/product/`,
  `docs/accounting/`, `docs/users/` and `docs/operations/` authorities. The
  historical prompts are provenance only.
- Branch protection, no-self-merge, distinct-role identity and production
  recovery need GitHub/CI enforcement. The repository implements advisory
  checks now and records the precise post-cutover activation gap.
