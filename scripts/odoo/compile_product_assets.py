"""Compile every deployable asset bundle declared by the installed registry.

Run with ``odoo shell --database=... < this_file``.  The database used for
qualification is disposable, so generated ``ir.attachment`` asset artifacts
may be committed normally by the shell transaction.
"""

# Odoo shell provides ``env`` as a global and the final line is the contract.
# ruff: noqa: F821, T201

from odoo.modules import Manifest

installed_modules = (
    env["ir.module.module"]
    .sudo()
    .search([("state", "=", "installed")], order="name")
    .mapped("name")
)
bundles = set(
    env["ir.asset"]
    .sudo()
    .with_context(active_test=False)
    .search([])
    .mapped("bundle"),
)
for module_name in installed_modules:
    manifest = Manifest.for_addon(module_name)
    if manifest:
        bundles.update((manifest.raw_value("assets") or {}).keys())

# Odoo manifests also name partial patch/variable bundles.  They are inputs to
# a deployable bundle and cannot be compiled alone (a ``before``/``replace``
# target may exist only in the parent).  Public bundles follow Odoo's
# ``<namespace>.assets[_...]`` convention.  Test-only roots are covered by the
# JavaScript suites rather than release-image compilation.
deployable_bundles = {
    bundle
    for bundle in bundles
    if isinstance(bundle, str)
    and (
        bundle.partition(".")[2] == "assets"
        or bundle.partition(".")[2].startswith("assets_")
    )
    and not bundle.endswith(("_tests", "_unit_tests"))
}

qweb = env["ir.qweb"].sudo()
compiled_nodes = 0
for bundle in sorted(deployable_bundles):
    nodes = qweb._get_asset_nodes(bundle, css=True, js=True, debug=False)
    compiled_nodes += len(nodes)

print(
    "Product asset compilation: PASS "
    f"({len(installed_modules)} modules, {len(deployable_bundles)} bundles, "
    f"{compiled_nodes} generated nodes)",
)
