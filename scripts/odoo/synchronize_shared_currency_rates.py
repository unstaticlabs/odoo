"""Align existing ECB rows across opted-in same-currency companies."""

import json


companies = env["res.company"].sudo().search(  # noqa: F821
    [
        ("rebuild_currency_rate_provider", "=", "ecb"),
        ("rebuild_currency_rate_share_same_base", "=", True),
    ],
    order="id",
)
companies._rebuild_synchronize_existing_shared_ecb_rates()
Rate = env["res.currency.rate"].sudo()  # noqa: F821
counts = {
    company.display_name: Rate.search_count(
        [
            ("company_id", "=", company.id),
            ("rebuild_rate_provider", "=", "ecb"),
        ],
    )
    for company in companies
}
env.cr.commit()  # noqa: F821
print(
    json.dumps(
        {
            "companies": companies.mapped("display_name"),
            "provider_rate_counts": counts,
        },
        indent=2,
        sort_keys=True,
    ),
)
