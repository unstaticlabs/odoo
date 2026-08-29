---
name: odoo-ui-product-quality
description: Qualify user-facing Odoo forms, lists, dialogs, dashboards, OWL components, responsive layouts, and end-to-end journeys through browser and screenshot evidence. Use for meaningful UI implementation, redesign, product QA, accessibility review, or visual repair.
---

# Odoo UI and Product Quality

Read the repository's Impeccable skill and its required `PRODUCT.md` before visual design work. Impeccable governs visual craft; this skill governs Odoo product qualification and evidence.

## Define the journey

State the user, permissions, company, starting state, task, expected result, and important failure/empty/loading states. Include desktop and narrow/mobile conditions when the surface is expected to be responsive.

## Iterate with the running product

1. Deploy or update a feature-specific QA environment when practical.
2. Use a real browser to perform the journey as the intended non-superuser identity.
3. Capture screenshots at decision points and at relevant viewport sizes.
4. Critique usability, information hierarchy, Odoo consistency, affordances, defaults, validation/errors, empty/loading/error states, responsiveness, keyboard use, focus, contrast, labels, and unnecessary complexity.
5. Repair the implementation and repeat browser → screenshot → critique → repair until material defects are resolved.

Prefer native Odoo components and interaction semantics. UI hiding is not access control; verify ACLs and record rules separately. Do not claim browser/product QA from static source inspection. When a browser or representative data is unavailable, record the exact limitation and mark the journey unverified.

Store or link concise evidence outside source history unless a durable product document needs it. Record the journey, result, evidence location, viewport, identity, database, branch, and SHA in the task or pull request.
