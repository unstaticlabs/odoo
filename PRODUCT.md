# Product

## Platform

web

## Users

The primary users are Valentin, Prosper, accountants and operations staff.
They use the system during daily French SME accounting, B2C commerce,
inventory, document review and operational follow-up. Specialist agents may
perform bounded repeatable work, while humans retain judgment, approval and
accountability.

## Product Purpose

The USL Odoo Distribution is the structured operating core of the USL
Automated Organization. It replaces the Odoo Online capabilities USL uses and
turns business, accounting and operational events into reliable shared state.
Success means that a small team can work efficiently from clear status,
responsibility and evidence without weakening accounting truth, security or
auditability.

## Positioning

The product combines native Odoo business workflows with USL-specific,
company-scoped operational controls and bounded agent participation. Odoo owns
the structured business interpretation and consequences of external events;
it does not attempt to duplicate every source system or manufacture missing
evidence.

## Operating Context

- French-first operational and accounting workflows in Odoo.
- Accounting, B2C sales, products, inventory, expenses, payroll, projects and
  Paperless-backed Documents share one governed operating environment.
- Users inspect evidence, resolve exceptions, approve sensitive actions and
  collaborate with accountants across explicitly permitted companies.
- Dense accounting and operational work is desktop-first. Mobile access must
  remain usable for review, capture and focused actions.
- External services remain authoritative for their original events and files;
  Odoo retains the relationships, state and business consequences needed to
  operate them.

## Capabilities and Constraints

- Preserve native Odoo navigation, controls, terminology and data semantics
  unless a documented product requirement justifies an extension.
- Prefer isolated product add-ons and maintained OCA functionality over core
  Odoo divergence.
- Keep migration extraction, source matching and reconstruction outside the
  delivered product interface.
- Preserve company isolation, role-based access, privacy boundaries,
  reconciliation state and immutable accounting history.
- Failed or incomplete automation must remain visible and actionable; it must
  never silently fabricate or duplicate business consequences.
- French terminology follows `docs/product/french-localization.md`.
- Electronic-invoice and e-reporting live operations remain disabled outside
  their approved production activation runbooks.

## Brand Commitments

The product name is **USL Odoo Distribution**. Its voice is direct, calm and
operational: explain what happened, why it matters, the recommended action and
the consequence of action or inaction. Familiar Odoo interaction patterns are
a product commitment, not an aesthetic limitation.

## Evidence on Hand

- Evergreen product specifications under `docs/product/`.
- Accounting requirements and invariants under `docs/accounting/`.
- User roles and workflows under `docs/users/`.
- Operational and deployment requirements under `docs/operations/`.
- Existing Odoo XML, JavaScript and SCSS implementations under
  `custom-addons/`, with automated backend and frontend tests.

Future design work must not invent customers, testimonials, financial claims,
source evidence or operational capabilities that are not present in these
authoritative sources.

## Product Principles

1. Reserve human attention for prepared decisions, approvals and genuine
   ambiguity.
2. Prefer opinionated simplicity and progressive disclosure over avoidable
   configuration or visual noise.
3. Make state, responsibility, evidence and the next action immediately clear.
4. Preserve native business truth, reversibility and auditable consequences.
5. Keep automation bounded, visible and safe across company and privacy
   boundaries.

## Accessibility & Inclusion

Actions must expose semantic roles, accessible names, keyboard activation and
visible focus. Disabled and read-only states must be distinguishable without
relying on pointer appearance or color alone. Interfaces must tolerate French
copy length, responsive layouts and mobile review without concealing critical
state or actions.
