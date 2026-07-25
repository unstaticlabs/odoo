# Configurable Accounting Framework

## Product contract

Accounting Controls, Reports and Declarations are governed definitions under
**Accounting > Configuration > Accounting > Accounting Framework**. They share
the following contract:

- a stable business code and definition version;
- standard Odoo, OCA, localization, USL or company-specific origin;
- current, draft or deprecated lifecycle;
- optional effective dates and company scope;
- business purpose and expected outcome;
- an installed source module and inspectable technical boundary.

Configuration and runtime remain separate:

| Definition | Operational result |
| --- | --- |
| Accounting Control | Hygiene issue or Closing control result |
| Accounting Report | transient interactive report session and PDF/XLSX export |
| Declaration | company/fiscal-period filing instance and its field results |

Every result or export freezes the definition version and a structured
provenance snapshot. Later configuration changes therefore do not obscure which
definition produced historical evidence.

## Architecture decision

Two credible approaches were considered:

1. expose a generic expression or Python builder for all three concepts;
2. retain specialized Odoo/OCA/USL engines and govern them through shared
   business definitions with whitelisted technical keys.

The second approach is implemented. Odoo models and maintained OCA modules
remain the accounting foundation, and each product surface retains its
professional interaction. Ordinary configuration cannot execute arbitrary
Python, SQL or JavaScript.

Source-report structures reconstructed from Enterprise remain migration
evidence under the technical migration area. They are not reused as the normal
report-definition model and are not exposed in Accounting Configuration.

## Upgrade and customization rules

Shared report definitions and French localization declaration definitions are
upgrade-managed. An Accounting Manager uses **Customize for Company** to create
a company override, then edits that override. Resolution prefers an effective
company definition over the shared definition with the same code.

Declaration seed records are `noupdate` data. Module upgrades add missing
standard definitions and governance metadata but do not overwrite configured
company definitions. Controls are already company-scoped; changing business
policy marks their origin as Company-specific.

Material changes to applicability, calculations, hierarchy, filters, readiness
policy or filing semantics require a new definition version. Deprecate an old
definition only after historical results have frozen its provenance.

## Runtime use

- Hygiene and Closing execute only enabled, current and effective Controls.
- The report client resolves the active Report for the selected company and
  period. Its filter/export capabilities and default hierarchy govern the
  runtime session.
- Declaration synchronization resolves current rules by country, company and
  fiscal period. Company definitions override matching localization rules.
- Disabled or deprecated definitions do not silently fall back around a
  company override.

These structured definitions and snapshots are the intended future MCP
inspection boundary. An agent must consume the same records and access rules as
the Odoo UI.
