---
name: odoo-access-control-safety
description: Design or review Odoo ACLs, record rules, multi-company boundaries, service identities, sudo use, controllers, and destructive actions.
---

# Enforce access server-side

- Menus, views, domains, and hidden buttons are not authorization. Check model
  ACLs, record rules, field groups, controllers, cron identities, and related
  records such as employees and attachments.
- Odoo record rules are default-allow after ACLs. Global rules intersect;
  group-scoped rules can expand access. Model the intended denials explicitly.
- Treat `sudo()` and raw SQL as security-boundary bypasses. Keep elevation to the
  smallest operation, validate the caller and target companies first, and never
  reuse an elevated recordset for user-visible results.
- For company-owned relations, use the correct company context and Odoo's
  `_check_company_auto`/`check_company` facilities where applicable. Do not infer
  safety from the currently selected companies.
- Test allowed and forbidden read/write/create/unlink paths with ordinary users,
  single-company and multi-company contexts, crafted RPC/API calls, and cron or
  service accounts. Include indirect access through linked records.
- Destructive or irreversible actions need explicit authorization, concurrency
  and idempotency protection, durable evidence, and a credible recovery path.

References: [Odoo security](https://www.odoo.com/documentation/19.0/developer/reference/backend/security.html), [multi-company guidelines](https://www.odoo.com/documentation/19.0/developer/howtos/company.html).
