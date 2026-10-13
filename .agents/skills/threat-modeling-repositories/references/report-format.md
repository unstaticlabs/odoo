# Threat model report format

```markdown
# Threat model: <scope>

## Scope and assumptions
- In scope / out of scope
- Deployment and exposure assumptions
- Evidence gaps that affect ranking

## System model
- Components and entry points
- Data flows and trust boundaries
- Assets and existing controls

## Attacker model
- Capabilities
- Important non-capabilities

## Prioritized abuse paths
### <threat>
- Preconditions and path:
- Boundary and affected assets:
- Existing controls:
- Likelihood / impact / confidence / priority:
- Evidence:
- Mitigations:

## Cross-cutting mitigations
<controls that break several important paths>

## Coverage limits and open questions
<unassessed paths, missing context, and conditional conclusions>
```

Keep architectural claims linked to repository evidence. Do not substitute threat-category names for abuse-path reasoning.
