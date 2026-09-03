---
name: odoo-ui-product-quality
description: Design and qualify polished Odoo forms, lists, dialogs, dashboards, OWL components, navigation, and responsive user journeys.
---

# Qualify the changed journey

- Start from the intended user, companies, record state, entry point, action,
  and expected destination. Include empty, loading, validation, access-denied,
  and stale-record behavior when relevant.
- Make the primary task visually obvious. Use a consistent grid, alignment,
  spacing, type scale, and restrained color hierarchy; group related content by
  proximity. Give secondary information less weight instead of adding decoration.
- Make interactivity legible and predictable. Distinguish enabled, disabled,
  hover, focus, selected, pending, success, and error states without relying on
  color alone. Preserve visible focus, logical keyboard order, and useful reflow.
- Prefer native Odoo components, services, actions, navigation, and responsive
  conventions. A visually correct screen with a wrong domain, company context,
  permission, URL, or browser-history behavior is defective.
- Put model logic in Python tests. Use HOOT with `web_test_helpers` for isolated
  frontend behavior and a tour when Python and JavaScript must work together.
- For a material user-facing change, exercise only the affected journey in a
  real browser as the intended non-admin user. Check desktop and narrow/mobile
  layouts when the surface supports them; inspect console/RPC failures and
  verify the resulting record state.
- Do not weaken record rules to make the UI work. Test authorization separately,
  and report a journey as unverified when representative identity or data is
  unavailable.

References: [Odoo testing](https://www.odoo.com/documentation/19.0/developer/reference/addons/testing.html), [visual hierarchy](https://www.nngroup.com/articles/principles-visual-design/), [WCAG 2.2](https://www.w3.org/TR/WCAG22/).
