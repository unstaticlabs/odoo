"""Remove reviewed optional auto-installs from the Distribution product registry."""

# Odoo shell script; ``env`` is supplied by Odoo.
# ruff: noqa: F821, T201

EXCLUDED_AUTO_INSTALL_MODULES = {
    "gamification",
    "hr_gamification",
    "hr_skills_survey",
    "html_builder",
    "link_tracker",
    "mail_plugin",
    "mass_mailing",
    "mass_mailing_sale",
    "mass_mailing_themes",
    "project_mail_plugin",
    "social_media",
    "survey",
}
ACTIVE_STATES = {"installed", "to install", "to upgrade", "to remove"}

Module = env["ir.module.module"].sudo()  # noqa: F821, N816
excluded = Module.search([
    ("name", "in", sorted(EXCLUDED_AUTO_INSTALL_MODULES)),
    ("state", "in", sorted(ACTIVE_STATES)),
])

if excluded:
    downstream = excluded.downstream_dependencies()
    unexpected = downstream.filtered(
        lambda module: (
            module.name not in EXCLUDED_AUTO_INSTALL_MODULES
            and module.state in ACTIVE_STATES
        ),
    )
    if unexpected:
        details = ", ".join(
            f"{module.name} ({module.state})"
            for module in unexpected.sorted("name")
        )
        raise RuntimeError(
            "Refusing to remove excluded optional modules because supported "
            f"installed modules depend on them: {details}",
        )

    names = ", ".join(excluded.sorted("name").mapped("name"))
    excluded.button_immediate_uninstall()
    print(f"Removed excluded optional product modules: {names}")
else:
    print("Excluded optional product modules: already absent")
