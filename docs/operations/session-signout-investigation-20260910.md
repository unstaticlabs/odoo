# Odoo sign-outs that no release explains — 2026-09-10

People are signed out of Odoo and have to go through Pocket ID again. The
reflex is to blame the release: it was blamed, and it was wrong. This records
what was measured, what it ruled out, and the instrumentation that will name
the cause the next time it happens, so nobody repeats the afternoon.

## What the release does, measured rather than assumed

`operations/stack.py` carries the outgoing generation's session store into the
candidate on a same-target rollout. Against `prod-odoo-nbg1-2` on 2026-09-10,
read-only:

- Every production release since 2026-09-08 records
  `restore / session-store / preserved`.
- The rollback generation volume held 2 278 session files. The live volume held
  all but three of them, and each of the three is explained by an ordinary
  rotation after cutover.
- Identifier `01uZsE…` (uid 8) and `T45dWJ…` (uid 5) were created **before** the
  2026-09-09 10:22 rollout and kept inserting `res.device.log` rows at 12:05,
  13:09, 16:07, 16:23 and later — **after** it. They were not signed out.
- The counts reconcile exactly: 2 278 carried in at 10:22 plus 940 minted since
  at one a minute equals the 3 218 then on disk.

The release preserves sessions. It is not the cause.

## What is actually happening

Authenticated sessions disappear **between** releases, after several hours of
inactivity, and the whole session file is gone rather than replaced:

- uid 5's session `T45dWJ…` was last used 2026-09-09 16:23 and was signed out by
  01:49 the next morning. No release ran in that window.
- uid 5's session `3uNuBm…` was last used 09:14 and never returned.
- Both identifiers are absent from `sessions/` entirely — not rotated to a new
  session id, which would keep the leading 42 characters. Meanwhile an orphaned
  rotation stub from 09-08 (`qCLjp6…`, holding `next_sid` and `deletion_time`)
  is still on disk, which is the opposite of what a working rotation leaves.

Ruled out by measurement, not by reasoning:

| Suspect | Why it is not this |
|---|---|
| Session lifetime | Odoo's default week; `sessions.max_inactivity_seconds` is unset |
| Cookie expiry | `Set-Cookie: session_id=…; Max-Age=604800` |
| `database.secret` rotating | `ir_config_parameter.write_date` unchanged since 2026-09-01 |
| A `res.users` change invalidating the token | `write_date` for uid 5 and uid 8 only ever moves at their own login |
| `SessionExpiredException` | No occurrence in the Odoo container log |
| The scheduled vacuum | Threshold is a week; the files were hours old |

A whole identifier vanishing points at `logout()` (a hard rotation deletes the
file) or `SessionStore.delete_from_identifiers` (which removes every file
sharing an identifier). Neither was traced to a caller from logs alone, because
neither logs anything.

## The instrumentation

`odoo/http/session.py` carries `USL-TRACE` logging at INFO on every path that
can remove a session:

| Message | What it means |
|---|---|
| `USL-TRACE sign-out` | `logout()` ran, and on which route |
| `USL-TRACE expired` | `check()` refused: the session outlived its `deletion_time` |
| `USL-TRACE token mismatch` | `check()` refused: a `res.users` column or the database secret moved |
| `USL-TRACE file deleted` | a hard rotation removed one file |
| `USL-TRACE rotated` | a rotation, with the id it moved from and to — *not* a disappearance |
| `USL-TRACE vacuum` | the scheduled reaper ran, and with what threshold |
| `USL-TRACE identifiers cleared` | the only path that removes every file sharing an identifier |

Volume is small: production holds five signed-in sessions, and anonymous
sessions are not traced at all.

**It logs no session material.** Session ids appear as the leading 8 characters,
which is what upstream already logs in `update_device_fingerprint`, and which is
shorter than the 42 `res.device.log` stores in the database. Routes go through
`usl_trace_route()`, which prints a path only when it starts with `/web/`,
`/odoo` or `/auth_oauth/` and reports `(other)` otherwise —
`/agent-documents/<grant>` carries a bearer token in its path, and the gateway
sets both `access_log off` and `error_log /dev/null` on that route to keep it
out of logs. A route added later falls into `(other)` by default.

`scripts/tests/test_session_signout_trace.py` holds those two properties.

## Reading it

```bash
ssh odoo 'docker logs usl-odoo-production-main-odoo-1 --since 24h 2>&1 | grep USL-TRACE'
```

The question to answer is which message precedes an identifier's last
appearance. `identifiers cleared` means something called
`delete_from_identifiers`; `sign-out` with a route names the request that did
it; neither appearing means the file went without Odoo removing it, which
points outside Odoo.

## Upgrade cost, and removing it

This is a distribution-level patch to an upstream file. It is additive — log
statements, one helper and three counters — and touches `logout`, `check`,
`SessionStore.delete`, `.rotate`, `.vacuum` and `.delete_from_identifiers`. An
upstream catch-up that rewrites any of those will conflict; the resolution is
to take upstream's version and re-apply the log line, or to drop it if the
cause is already known.

**Remove it once the cause is named.** `grep -n USL-TRACE odoo/http/session.py`
is the complete list, and the test above asserts that it stays so.
