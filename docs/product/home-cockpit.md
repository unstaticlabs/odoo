# Home cockpit

Home is the default landing action for internal users. It keeps the native
Odoo shell and summarizes only work that calls for attention before routing
the user into the complete native workflow.

## Widgets

- **Activities** shows at most five readable activities assigned to the current
  user, ordered by deadline then record ID. This means overdue items precede
  today, which precedes future work, without inventing a second priority model.
- **My Tasks** uses the canonical My Tasks domain. It reports the largest open
  stage groups and counts overdue, seven-day, waiting and changes-requested
  work without loading the task backlog. Every count opens as a named search
  facet whose domain matches that exact count and survives browser history or
  refresh.
- **Favorite Views** stores current-user destinations as native actions,
  structured view state, records or curated system destinations. Raw URLs and
  executable expressions are not stored.
- **AI Pipelines** discovers Project workspaces at runtime from the tags
  `Agent Ready`, `Agent Failed`, `Needs Human`, `Human Approved`, `Has PR` and
  `Pipeline`. It surfaces assigned open work marked `Agent Failed`, `Blocked`
  or `Needs Human`, plus changes-requested and Review-stage work. Renaming these
  operational tags intentionally changes discovery.
- **Accounting & Compliance Alerts** reads every selected company's
  `rebuild.account.overview`. Counts are additive and combined by default,
  with the non-zero per-company contributions visible on each tile. Links open
  the matching selected-company population for closing, declaration, review,
  bank, evidence and hygiene workflows; no financial values or legal-company
  readiness states are consolidated.

Each provider runs under the current user's ACLs and record rules. A failed
provider does not prevent the others from loading. The header states whether
Home is showing one company or a combined selected-company scope. Activities,
My Tasks and AI Pipelines combine readable records in multi-company mode;
company-specific favorites remain labelled. Company changes reload the
available widgets and company-sensitive data without changing the user's
selected-company mode.

## Personalization and extension

Widget visibility and order live in `res.users.settings.usl_home_layout`.
Favorites belong to `usl.home.favorite` and are protected by an own-user rule.
Unavailable favorites retain only a generic removable placeholder in Home.
Favorite rows identify their destination type (project, saved view, accounting,
or AI workspace) so a user can distinguish two similarly named routes without
opening them. The surface uses a compact two-column destination list on wider
screens and one column on narrow screens; this increases useful density without
introducing another launcher or hiding keyboard order.

Valentin starts with an opinionated layout based on his available Projects,
saved views, Accounting workspaces and My Tasks. The defaults never store
external source identifiers in `usl_home`. Redundant Activities and temporary
operational filters remain available in their native applications but do not
crowd Home. Personalization belongs to the user and product upgrades must not
reset it.

New widgets must use a bounded provider method on `usl.home.service`, declare a
stable widget key and availability rule, and navigate into an existing product
workflow. Home is not a generic dashboard or a second implementation of the
source application.
