Contributing to Odoo
====================

[Full contribution guidelines](https://github.com/odoo/odoo/wiki/Contributing)

TL;DR

* If you [make a pull request](https://github.com/odoo/odoo/wiki/Contributing#making-pull-requests),
  do not create an issue! Use the PR description for that
* Issues are handled with a much lower priority than pull requests
* Use this [template](https://github.com/odoo/odoo/wiki/Contributing#reporting-issues)
  when reporting issues. Please search for duplicates first!
* Pull requests must be made against the [correct version](https://github.com/odoo/odoo/wiki/Contributing#against-which-version-should-i-submit-a-patch)
* There are restrictions on the kind of [changes allowed in stable series](https://github.com/odoo/odoo/wiki/Contributing#what-does-stable-mean)


## About AI Agents

This repository is maintained by human and AI contributors.

All contributions must preserve:

- compatibility with upstream Odoo;
- architectural consistency;
- accounting correctness;
- security and privacy;
- upgradeability;
- traceability;
- operational reliability.

Read `ARCHITECTURE.md`, `ROADMAP.md`, and relevant decision records before making material changes.


## About this fork

- Prefer standard Odoo functionality.
- Prefer supported extension points over core modifications.
- Reuse maintained OCA modules where appropriate.
- Avoid parallel sources of truth.
- Keep custom modules focused and modular.
- Preserve standard accounting semantics.
- Make failures visible and recoverable.
- Design repeated execution to be safe.
- Keep agents within bounded permissions.
- Document material divergence from upstream.


## Before starting

Confirm:

- the product requirement is clear;
- the relevant existing code has been inspected;
- current Odoo documentation and source have been reviewed;
- maintained alternatives have been considered;
- dependencies and affected modules are understood;
- accounting, data, privacy, and upgrade risks have been identified.

For material architectural choices, present at least two credible options with their pros and cons before implementation.


## Development workflow

1. Create or select an issue.
2. State the intended outcome and acceptance criteria.
3. Work in a dedicated branch.
4. Keep changes limited to the requirement.
5. Add or update tests.
6. Update documentation where behaviour changes.
7. Run the required local checks.
8. Open a pull request.
9. Address review findings.
10. Merge only after required checks and approvals pass.

Do not commit directly to the protected primary branch.


## Code expectations

Contributions must:

- follow existing Odoo conventions;
- use clear module boundaries;
- avoid unnecessary abstraction;
- avoid hidden side effects;
- avoid duplicated business logic;
- use the ORM for normal business operations;
- preserve company and permission boundaries;
- remain understandable without relying on the original contributor;
- include a versioned data-upgrade path when persisted data changes.

Direct modifications to upstream Odoo code require explicit architectural approval and documentation.


## Testing

Add tests proportional to risk.

At minimum, verify:

- the requested behaviour;
- failure behaviour;
- permissions;
- multi-company behaviour where relevant;
- duplicate execution and retries;
- module installation and upgrade;
- stored-data and upgrade impact where relevant.

Accounting-critical changes require realistic accounting fixtures and report validation.

Every fixed regression must receive a regression test.


## AI contributor requirements

AI contributors must:

- inspect relevant code before editing;
- verify assumptions against source and documentation;
- avoid inventing unavailable APIs or models;
- keep a clear record of changed files;
- state uncertainty explicitly;
- run available tests;
- report failures honestly;
- avoid broad unrelated refactors;
- avoid changing generated, vendored, or upstream code without justification.

AI-generated work receives the same review and quality requirements as human-generated work.

An AI contribution must not be merged solely because it appears plausible.


## Pull requests

Each pull request should include:

- **Outcome:** what user or system need it addresses;
- **Approach:** what changed;
- **Alternatives:** options considered for material decisions;
- **Risks:** accounting, security, privacy, stored-data, performance, or upgrade impact;
- **Testing:** checks executed and their results;
- **Upstream impact:** whether standard Odoo code or behaviour is affected;
- **Known limitations:** unresolved issues or follow-up work.

Keep pull requests small enough to review reliably.


## Documentation

Update documentation when changing:

- system boundaries;
- module responsibilities;
- workflows;
- permissions;
- integrations;
- deployment behaviour;
- accounting behaviour;
- stored-data upgrade and recovery expectations.

Create or update an Architecture Decision Record for material architectural choices.


## Security and privacy

Never commit:

- passwords;
- API keys;
- access tokens;
- production backups;
- private customer, employee, creator, or accounting data.

Use sanitized fixtures.

Report suspected vulnerabilities privately through the process defined in `SECURITY.md`.


## Accounting changes

Accounting changes require additional care.

Do not:

- silently alter posted entries;
- bypass lock dates;
- delete audit evidence;
- create a parallel ledger;
- change tax or reconciliation semantics without explicit review.

Material accounting changes require review by the Technical Architect and functional validation by the appropriate accounting stakeholder.


## Definition of Done

A contribution is complete when:

- the requirement is satisfied;
- tests pass;
- relevant risks were considered;
- permissions and failure behaviour were checked;
- documentation is current;
- stored-data and upgrade impact are understood;
- no unexplained upstream divergence was introduced;
- the repository remains coherent and maintainable.
