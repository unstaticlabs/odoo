# Identity restoration

The identity migration is a temporary, dump-bound translation stage. It runs
after Accounting has established the two legal companies and before later
business stages consume global Contacts. It is not installed in the delivered
Odoo product.

## What it restores

- every source Contact, parent/child relationship, company assignment, address,
  language, timezone, commercial field, contact tag, and industry;
- every bank account and its owner, company, trust state, and bank metadata;
- all six source user identities and their allowed companies;
- supported source access-group memberships by stable XML ID.

The Online administrator is explicitly mapped to the target `valentin` user,
whose login is managed by Pocket ID. Odoo's root, public, and portal-template
users remain target-native runtime accounts. A non-native portal identity is
restored inactive with Portal semantics. Product-specific target groups are
preserved; the importer adds supported source permissions instead of replacing
the target role policy.

Passwords, password hashes, TOTP secrets, API keys, OAuth identifiers, sessions,
devices, and login history are deliberately absent from the source reader.
Users re-enroll in Pocket ID.

## Why a temporary add-on

Odoo's generic CSV importer is useful for ordinary data entry, but it cannot
prove dump identity, retry cross-model relationships safely, distinguish
runtime users from business users, or compare canonical source/target digests.
The selected implementation is a temporary add-on mounted only on the migration
service. The alternative—keeping source IDs on product models permanently—was
rejected because it would make migration mechanics part of the distribution.

## Run and validate

Use an isolated Compose project and the exact protected source package:

```bash
COMPOSE_PROJECT_NAME=codex-migration-identity \
USL_ONLINE_DUMP_DIR=/absolute/path/to/usl-online-dump \
make identity-restore
```

For stepwise diagnosis:

```bash
make identity-restore-install
make identity-restore-import
make identity-restore-validate
make identity-restore-finalize
```

The validator requires exact counts and SHA-256 parity for material Contact,
user, bank, category, industry, company-membership, and contact-tag data. It
also verifies every supported group mapping and emits the stable XML IDs of
group semantics delegated to incomplete product scopes.

Re-running import before finalization is idempotent. Reinstalling the temporary
module after finalization reclaims the same natural records rather than creating
duplicates. Finalization uninstalls the migration module and asserts that the
business row counts do not change; the product/migration boundary then checks
that no temporary model, field, access rule, or XML ID remains.
