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
  work without loading the task backlog.
- **Favorite Views** stores current-user destinations as native actions,
  structured view state, records or curated system destinations. Raw URLs and
  executable expressions are not stored.
- **AI Pipelines** discovers Project workspaces at runtime from the tags
  `Agent Ready`, `Agent Failed`, `Needs Human`, `Human Approved`, `Has PR` and
  `Pipeline`. It surfaces assigned open work marked `Agent Failed`, `Blocked`
  or `Needs Human`, plus changes-requested and Review-stage work. Renaming these
  operational tags intentionally changes discovery.
- **Accounting & Compliance Alerts** reads the current active company's
  `rebuild.account.overview` and links to existing closing, declaration,
  review, bank, evidence and hygiene workflows. It never reports financial
  values or aggregates companies.

Each provider runs under the current user's ACLs and record rules. A failed
provider does not prevent the others from loading. Company changes reload the
available widgets and company-sensitive data.

## Personalization and extension

Widget visibility and order live in `res.users.settings.usl_home_layout`.
Favorites belong to `usl.home.favorite` and are protected by an own-user rule.
Unavailable favorites retain only a generic removable placeholder in Home.
Favorite rows identify their destination type (project, saved view, accounting,
or AI workspace) so a user can distinguish two similarly named routes without
opening them. The surface uses a compact two-column destination list on wider
screens and one column on narrow screens; this increases useful density without
introducing another launcher or hiding keyboard order.

The production reconstruction gives Valentin an opinionated first-run setup
from the protected Online dump. The temporary Identity preference stage uses
the restored administrator mapping, native saved filters, and restored Project
favorite relationships; it never stores source IDs in `usl_home`. It selects
My Tasks, at most four operational source-favorite projects, available AI and
Accounting workspaces, and the durable supplier-invoice and FY2526 bank-review
saved views. Redundant Activities and transient cutover filters remain
available in their native applications but do not crowd Home. Replaying the
migration is idempotent; personalization after the shipped migration remains
owned by Valentin and is not reset by product upgrades.

New widgets must use a bounded provider method on `usl.home.service`, declare a
stable widget key and availability rule, and navigate into an existing product
workflow. Home is not a generic dashboard or a second implementation of the
source application.
