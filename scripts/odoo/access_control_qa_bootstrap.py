# ruff: noqa: EM101, F821, I001, T201

"""Prepare the minimal company identity for isolated access-control QA.

This script is test support, not product data. It only renames Odoo's untouched
default fixture company and refuses an ambiguous or reconstructed database.
"""

from odoo.exceptions import ValidationError


companies = env["res.company"].with_context(active_test=False).search([])  # noqa: F821
unstatic = companies.filtered(lambda company: company.name == "Unstatic Labs")
if len(unstatic) > 1:
    raise ValidationError("More than one company is named Unstatic Labs.")
if not unstatic:
    if len(companies) != 1 or companies.name != "My Company":
        raise ValidationError(
            "Access-control QA bootstrap requires one untouched 'My Company' fixture.",
        )
    companies.write({"name": "Unstatic Labs"})
    env.cr.commit()  # noqa: F821
    unstatic = companies

print(f"Access-control QA company ready: {unstatic.name} (id={unstatic.id})")  # noqa: T201
