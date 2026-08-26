# ruff: noqa: EM101, F821, I001, T201

"""Prepare the product company identities for isolated access-control QA.

This script is test support, not product data. It renames Odoo's untouched
default fixture company, adds the second product company, and refuses an
ambiguous or reconstructed database.
"""

from odoo.exceptions import ValidationError


companies = env["res.company"].with_context(active_test=False).search([])  # noqa: F821
unstatic = companies.filtered(lambda company: company.name == "Unstatic Labs")
media = companies.filtered(lambda company: company.name == "USL MEDIA")
if len(unstatic) > 1:
    raise ValidationError("More than one company is named Unstatic Labs.")
if len(media) > 1:
    raise ValidationError("More than one company is named USL MEDIA.")
if not unstatic:
    if len(companies) != 1 or companies.name != "My Company":
        raise ValidationError(
            "Access-control QA bootstrap requires one untouched 'My Company' fixture.",
        )
    companies.write({"name": "Unstatic Labs"})
    env.cr.commit()  # noqa: F821
    unstatic = companies
if not media:
    unexpected = companies - unstatic
    if unexpected:
        raise ValidationError(
            "Access-control QA bootstrap found unexpected companies: "
            + ", ".join(unexpected.mapped("name")),
        )
    media = env["res.company"].create({"name": "USL MEDIA"})  # noqa: F821
    env.cr.commit()  # noqa: F821

print(  # noqa: T201
    "Access-control QA companies ready: "
    f"{unstatic.name} (id={unstatic.id}), {media.name} (id={media.id})",
)
