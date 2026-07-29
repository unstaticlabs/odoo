# USL Bootstrap

Technical module name: `usl_bootstrap`

This is an isolated synthetic fixture for installation tests and local smoke
experiments. It is not a production product module, is not a dependency of
USL Accounting, and must not be installed in the canonical development,
reconstruction, QA or production databases.

Use an explicitly named disposable database as documented in the root
`README.md`. The fixture never represents production-derived accounting truth.
