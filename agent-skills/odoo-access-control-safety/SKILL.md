---
name: odoo-access-control-safety
description: Design and review Odoo ACLs, record rules, multi-company boundaries, agent accounts, irreversible-action guards, audited operations, sudo usage, and destructive workflows. Use whenever a change affects who can see or mutate data or introduces dangerous actions.
---

# Odoo Access Control and Agent Safety

Treat server-side permissions as the boundary. Menus, views, button visibility, domains, and disabled controls improve UX but do not enforce authorization.

1. Enumerate actors, companies, records, operations, and required denials. Include portal/public and agent identities when applicable.
2. Inspect model ACLs, record rules, field groups, controller/API checks, cron/service identities, multi-company context, and implicit access through related models or attachments.
3. Use the narrowest privilege. Avoid `sudo()`; where unavoidable, isolate it, validate inputs and record ownership before escalation, minimize the elevated region, and preserve an audit trail.
4. Put irreversible or destructive operations behind explicit authorization, confirmation, idempotency/concurrency guards, and durable evidence. Prefer reversible state transitions and recovery paths.
5. Verify allowed and forbidden behavior with non-admin test users. Test cross-company and crafted RPC/API access, not only button visibility.
6. Keep development agents away from production credentials and live external side effects. Follow repository electronic-invoice live-safety flags and production-data rules.
7. Record permissions changed, negative tests, destructive/irreversible effects, audit behavior, and remaining enforcement gaps in the handoff.

The repository's Irreversibility add-on can support a control; it does not replace ACL, record-rule, data-integrity, backup, or recovery analysis.
