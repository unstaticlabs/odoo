# SaaS 19.3 alignment register

## Frozen inputs

- Upstream: `upstream/saas-19.3` at
  `efb98f932f3a568ce550a26ebde06da0e14e65d3` (23 August 2026).
- Previous Distribution: `19-usl` at
  `627e0e52995de4a93f3c4e55db545bbc3d1c11c7`, preserved as
  `archive/19-usl-pre-saas-19.3-20260824`.
- Online source: Odoo `saas~19.3.1.3`, dump SHA-256
  `0b9916db4807206f63b654bd2933ac89b0aab30ba7e0a1004edc4c060490238f`.
- Multi-company source: `a9d27c4b8f164142f9d120a41b15c29d3b76b2e3`, preserved as
  `archive/feat-multicompany-accounting-pre-saas-19.3-20260824`.

Source and target use the same Odoo generation. Reconstruction translates the
Online Enterprise data into native Community, pinned OCA and USL product
records; it is not an in-place database upgrade.

## Integration decisions

- The 19.2 and 19.3 upstream histories were not merged. The verified USL
  final-state delta was replayed on the frozen 19.3 upstream commit.
- The custom fiscal-year sequence and resequencing corrections remain because
  19.3 does not provide equivalent behavior.
- Multi-company is part of the qualified Distribution. Stable operational
  models, tables and XML IDs remain unchanged.
- Active runtime and reconstruction identifiers use 19.3. Historical
  `saas~19.2.*` module migration directories and the 19.2 alignment record are
  retained as installed-version evidence.
- OCA modules remain pinned to reviewed 19.0 commits. Their SaaS compatibility
  adaptations live under `oca-patches/saas-19.3/` and are reapplied
  deterministically.
- SaaS 19.3 replaced `account.group` with parent accounts before the pinned
  OCA financial reports adopted that hierarchy. `usl_accounting` therefore
  retains the stable prefix-group model as a tested compatibility bridge; it
  can be removed only after OCA reports and reconstructed hierarchy parity use
  the native model.
- Both electronic-invoice live guards remain disabled throughout migration and
  qualification.

## Qualification record

The Distribution may be promoted only after the following evidence is current:

- clean product installation and two consecutive upgrades;
- USL and OCA backend and browser suites;
- company isolation, combined reports, shared ECB rates and role checks;
- complete fresh reconstruction from the frozen dump, repeated-import
  idempotence and zero migration residue;
- balanced accounting, attachment and reconciliation parity;
- pre-production, recovery and target-identity gates.

Validation results and any justified exclusions are recorded here before
promotion. The 19.2 register remains historical and must not be edited to
describe 19.3 evidence.
