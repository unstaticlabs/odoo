# Staging on demand

Staging shares one VPS (`prod-odoo-nbg1-2`) with production. Awake and idle it
holds about 1.7 GB of RAM across 13 service containers. It therefore sleeps by default:
every application service is stopped, everything that is *state* is kept, and
it wakes when a pipeline or a person needs it.

The lifecycle extends `usl-stack runtime`. Waking restores the recorded active
generation; it never selects the desired next release. An exact match starts
with `--no-recreate`, preserving container IDs. If a failed activation left a
different cohort behind, wake stops the application cohort and recreates it
against the active generation's original base Compose files, images, volumes
and networks. The gateway and persistent data stay in place.

## What it saves

Measured on `prod-odoo-nbg1-2` (4 vCPU, 7.7 GB RAM) on 2026-11-04, awake then
asleep, nothing else changed:

| | Awake | Asleep |
|---|---|---|
| Staging containers running | 14 (13 services + gateway) | 1 (gateway) |
| Staging memory (container sum) | about 1.7 GB | about 8 MB (gateway) |
| Host memory in use | 5.2 GB | 3.4 GB |
| Host memory available | 2.5 GB | 4.4 GB |
| Host processes | 1765 | 1445 |

The lifecycle daemon, a stack of its own, stays up in both columns (a few tens of MB, not in the table). Idle staging also used a few percent of one CPU (more while Odoo and Paperless
index), and every wake costs two to three minutes of start-up CPU. Sleeping
returns that to production.

## States

| State | Meaning |
|---|---|
| `active` | Every staging service runs. Also the state of a staging that has never slept. |
| `waking` | A wake is in progress. Written first, so a crash is visible. |
| `stopping` | A sleep is in progress. |
| `sleeping` | Every application service is stopped. The gateway stays up and serves the sleeping page. |
| `failed` | The last wake or sleep did not finish. `last_error` says why. A new `wake` or `sleep` retries. |

The state lives in `/var/lib/usl-odoo/runtime/staging/lifecycle.json` on the
host, with the last 30 events. `runtime status` and `runtime lifecycle` print
it. Container identity is checked against the active generation before a wake
reports `already-active`; a running count cannot prove which cohort is running.

## What is kept, what is stopped

Stopped: the 13 services in `services` of `operations/targets-host/staging.json`
(Odoo, PostgreSQL, Paperless and its database, broker, Gotenberg and Tika, MCP,
renderer, receipt fetcher and egress, Sign and its CA). Applications stop first
and PostgreSQL and Valkey last, with a 60 second grace period.

Kept: every volume, every generation (including the rollback generation),
snapshots, backups, release evidence and the gateway. Sleeping deletes nothing.

Staging does not restart by itself. `docker stop` marks a container as
manually stopped, and Docker does not start such a container at boot even with
`restart: unless-stopped`. The GitOps stack for staging deploys only through
the audited release jobs, which start the cohort explicitly.

## Commands

```bash
scripts/usl-stack --target staging runtime lifecycle        # state, next possible sleep, containers
scripts/usl-stack --target staging runtime wake             # start staging (two to three minutes)
scripts/usl-stack --target staging runtime sleep --force    # sleep now
scripts/usl-stack --target staging runtime lease --minutes 180 --holder manual:demo
scripts/usl-stack --target staging runtime idle-sweep       # one pass of the idle policy
scripts/usl-stack --target staging runtime touch --reason release:x   # record activity, start nothing
```

`wake` and `sleep` are idempotent. `--reason` is recorded; a reason starting
with `ci:`, `release:` or `pipeline:` gets the pipeline idle grace, anything else
the interactive one (both are currently 30 minutes).

`sleep` without `--force` obeys the idle policy. With or without `--force` it
refuses (`deferred`) while:

- a release, backup, restore or other runtime operation holds a lock;
- the gateway maintenance marker exists;
- a release attempt is unfinished;
- a lease is held.

Nothing outranks work in progress.

`wake` is as careful: unless a staging release calls it with `--may-resume`, it
starts nothing (`deferred`) while the maintenance marker exists or a release
attempt is unfinished, because a failed rollback may have left the cohort
stopped on purpose, waiting to be resumed. `touch` only records activity.
Every write to `lifecycle.json` re-reads the file under a short lock, so a
lease, a sweep and a release never overwrite one another with a stale copy.

## Recovery identity

A current generation records its canonical Compose provenance in
`generations/<generation>/compose.identity.json`. Older admitted generations
resolve their original GitOps commit through the digested admission receipt and
its matching attempt claim. Wake accepts only direct canonical paths and an
exact commit marker in the retained GitOps run. When several runs of the same
commit exist, identical canonical definitions are selected deterministically;
different definitions report an ambiguity.

Wake keeps independently admitted MCP authority, after checking that its
compatibility contract fits the active Odoo release. Its image, build ID and
revision label follow the selected MCP commit. The MCP override's
`.source.json` records its immutable GitOps source separately from Odoo's base.
Existing authority overrides and independent candidate/rollback YAML remain
valid when retained evidence proves their source. An unavailable source produces
an actionable failure; wake never substitutes the failed cohort's context.

The pre-receipt compatibility path can still start a generation from its own
captured Compose files. Adopted and legacy v2 layouts retain their existing
start semantics. These paths do not invent an admission receipt or change
`active.json`.

For a mismatch, logs and `lifecycle.json` name the active generation, affected
services and differing identity fields. A failed attempt releases its locks and
remains retryable after the missing context or runtime problem is repaired.
`--may-resume` permits recovery under a release's existing lock; it does not
change the selected generation or remove maintenance.

## Locks

A lifecycle operation takes `lifecycle-operation.lock`, then the host-wide
`release-operation.lock`, then the runtime lock. The GitOps launcher rejects a
release at once when the release lock is taken, except when
`lifecycle-operation.lock` is held too: then the holder is a bounded wake or
sleep and the launcher waits for it instead of failing the pipeline. A wake
called from inside a release (`--release-lock-held`) takes only the runtime
lock, and only a staging release is meant to add `--may-resume` (an operator can still pass it by hand as an override). A production release arriving during a wake or sleep waits for it, up to 45 minutes, instead of failing.

## Idle policy

Declared in the `lifecycle` block of the staging target, not in code:

| Field | Value | Meaning |
|---|---|---|
| `idle_after_ci_minutes` | 30 | Grace after the last pipeline activity or a pipeline-triggered wake. |
| `idle_after_interactive_minutes` | 30 | Grace after the last person using Odoo, or a person-triggered wake. |
| `max_lease_minutes` | 480 | Longest single lease; renew to keep going. |
| `wake_timeout_seconds` | 900 | Longest wait for Odoo to report healthy. |
| `sweep_seconds` | 60 | How often the daemon applies the policy. |

Staging goes to sleep at the latest of those instants. Production and local
targets declare `{"mode": "always_on"}` and cannot be put to sleep: only a
staging target may be `on_demand`.

**What counts as a person.** The daemon reads the staging gateway's access log.
Authenticated browser traffic counts: `POST` to `/web/dataset/call_kw`,
`/web/dataset/call_button`, `/web/action/load`, `/mail/…`, `/discuss/…`,
`/web/session/…`, and a completed `/websocket` upgrade. The login page, static
assets, `/web/health`, `robots.txt` and any client whose user agent looks
automated (bots, crawlers, uptime monitors, `curl`, `python-requests`) never
count, so they neither keep staging awake nor wake it.

## Waking

**From a pipeline.** A pipeline never signs in: it reaches the host through the
fixed control plane (GitLab job, Komodo action, exact intent, launcher as root).
The GitOps release launcher wakes staging before it touches it, passing
`--release-lock-held` because it already owns the host-wide release lock, and
records the end of the work with `touch` (which never starts anything, even after
a failed release). Qualification, tests, backups, deployment and rollback are
unchanged. About half an hour later the idle sweep puts staging back to sleep.

**From the web.** While staging sleeps, `https://odoo-staging.unstaticlabs.com`
answers `503` with a sleeping page. A person signs in with Pocket ID (the same
provider and the same `c_suite` group as staging Odoo) and presses **Wake
staging**; the page then opens staging by itself. The lifecycle daemon speaks
OpenID Connect itself (authorization code + PKCE, confidential client, userinfo
for the groups), so nothing depends on Odoo being up. The wake is recorded as
`web:<email>` in `lifecycle.json`, so the history says who woke staging.
Bots and probes cannot start a sign-in or a wake; a cross-site request is
refused; the session is a signed `HttpOnly; Secure; SameSite=Strict` cookie
that lasts eight hours and dies when the client secret is rotated. Health
probes (`/web/health`) receive `200 {"status":"sleeping"}` so a sleeping staging
does not page anyone; an *active* staging that does not answer yields a `502`.

The daemon signs in with **staging Odoo's own Pocket ID client**
(`POCKET_ID_CLIENT_ID` and `POCKET_ID_CLIENT_SECRET`, the Komodo variables the
staging stack already uses), so there is no second secret. Without them the page
says that web wake is not set up and only the command line works.

*One-time setup (needs a Pocket ID admin).* In Pocket ID, add this callback URL to
the existing staging Odoo client, beside its Odoo one:

```
https://odoo-staging.unstaticlabs.com/__usl/callback
```

That client is already restricted to the `c_suite` group. Rotating its secret
signs every wake session out; redeploy the lifecycle stack so the daemon picks up
the new value.

*Checking it end to end.* With staging asleep (`runtime lifecycle` says `sleeping`),
open `https://odoo-staging.unstaticlabs.com` in a browser:

1. The page says staging is asleep and offers **Sign in with Pocket ID**.
2. After signing in you come back to the same page, now showing your email and a
   **Wake staging** button (if you see the sign-in button again, the gateway is not
   forwarding the `usl_wake_*` cookies: it needs the staging release that carries
   the `gateway.conf` of unstaticlabs/infra/gitops!901).
3. **Wake staging** starts it; two to three minutes later the page opens Odoo.
4. `runtime lifecycle` shows `last_wake_reason: web:<your email>`.

**From the command line.** `scripts/usl-stack --target staging runtime wake`.
SSH access to the host is the authentication.

## Moving parts

| Part | Where | Role |
|---|---|---|
| `operations/staging_lifecycle.py` | this repository | Pure policy: states, idle decision, activity detection, sleeping page. |
| `operations/staging_wake_oidc.py` | this repository | Pocket ID sign-in for the wake page (code + PKCE, signed cookies). |
| `runtime wake|sleep|lease|lifecycle|idle-sweep` | `operations/stack.py` | Applies the policy with the existing Compose identity and locks. |
| `runtime lifecycle-daemon` | `operations/stack.py` | Serves the wake endpoint and runs the idle sweep. The only always-on piece (about 30 MB). |
| `usl-odoo-staging-lifecycle` stack | GitOps | Runs the daemon from the pinned operations image. |
| `gateway.conf` | GitOps | Routes `/__usl/` and upstream failures to the daemon. |
| `usl-odoo-release-launcher.py` | GitOps | Wakes staging before any staging operation. |

## Failure and recovery

- **A wake or sleep fails.** The state becomes `failed` with `last_error`; run
  the command again. Starting is `--no-recreate`, so it cannot damage anything.
- **The daemon is down.** Staging keeps whatever state it has; the gateway
  returns a plain `502` instead of the sleeping page. Wake with the command
  line, and restart the daemon stack.
- **A release is running.** Sleep defers; wake from inside the launcher does not
  deadlock on its own lock.
- **The host reboots.** Staging stays down if it was asleep; if it was awake,
  Docker restarts it as it always did.
- **Stuck in `waking` or `stopping`.** The daemon marks a transition that has not
  finished after three `wake_timeout_seconds` as `failed` (`Interrupted`). If a
  `usl-stack` process was killed mid-operation, `operation.lock` under the state
  directory stays behind and every wake, sleep and release refuses with "another
  operation already owns staging". Check that no `usl-stack` or `docker compose`
  process for the staging project is still running (a killed client leaves its
  remote `compose stop` to finish), then:

  ```bash
  ssh odoo -- 'cat /var/lib/usl-odoo/runtime/staging/operation.lock/owner.json'   # who held it, since when
  ssh odoo -- 'rm -f /var/lib/usl-odoo/runtime/staging/operation.lock/owner.json && rmdir /var/lib/usl-odoo/runtime/staging/operation.lock'
  scripts/usl-stack --target staging runtime wake      # or: runtime sleep --force
  ```

  Rehearsed on 2026-11-04: a `sleep` killed after 25 s left 10 of 13 services
  stopped and the state `stopping`; after removing the lock, `wake` started the
  remaining services and the state returned to `active` (13/13).
- **Cleanup while asleep.** `cleanup` protects the generations, networks and
  volumes of the stopped cohort exactly as it does for a running one.
