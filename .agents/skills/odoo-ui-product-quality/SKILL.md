---
name: odoo-ui-product-quality
description: Qualify meaningful Odoo forms, lists, dialogs, dashboards, OWL components, navigation, and responsive user journeys.
---

# Qualify the changed journey

- Start from the intended user, companies, record state, entry point, action,
  and expected destination. Include empty, loading, validation, access-denied,
  and stale-record behavior when relevant.
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

References: [Odoo testing](https://www.odoo.com/documentation/19.0/developer/reference/addons/testing.html), [HOOT](https://www.odoo.com/documentation/19.0/developer/reference/frontend/unit_testing/hoot.html).
