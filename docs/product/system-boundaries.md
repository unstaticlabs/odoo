# System boundaries

## Authoritative domains

- **Odoo:** structured operational and accounting truth.
- **GitHub:** code, engineering artefacts and pull requests.
- **Banks:** original financial events.
- **Gmail:** communications.
- **Google Calendar:** commitments.
- **Google Drive:** many source documents and evidence files.
- **Obsidian:** long-form knowledge.
- **Creator, commerce and social platforms:** source platform events and metrics.

Odoo stores the business interpretation, relationships, state and consequences needed to operate these external objects.

## Integration principles

- Do not duplicate complete external systems without a business reason.
- Preserve stable links and provenance.
- External failure must not corrupt internal state.
- Reprocessing must not duplicate business consequences.
- Integrations must expose delayed, partial and failed states.
- Provider-specific behaviour must not become the product's business model.

## Entity boundaries

The system explicitly distinguishes:

- legal companies;
- brands;
- products;
- projects;
- activities;
- analytic dimensions;
- personal economic entities.

Branding never determines legal or accounting ownership.
