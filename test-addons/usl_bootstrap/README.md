# USL Bootstrap

Technical module name: `usl_bootstrap`

This is an isolated synthetic fixture for installation tests and local smoke
experiments. It lives under `test-addons/`, not `custom-addons/`, so it is not
a product module: the product-module registry
(`operations.module_release.product_modules`) never lists it, no product image
carries it, and no database boundary needs to exclude it.

Only the Compose `test` profile mounts `test-addons/` as an addons path
(`/mnt/test-addons`). Install it into an explicitly named disposable database,
never into the canonical development, reconstruction, QA or production
databases. The fixture never represents production-derived accounting truth.
