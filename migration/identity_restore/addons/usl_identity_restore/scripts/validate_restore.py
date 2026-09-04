# ruff: noqa: F821, T201

import hashlib
import json
from decimal import Decimal

from odoo.addons.usl_identity_restore.models.restore import (
    DROPPED_AI_EXPORT_IDS,
    DROPPED_SALES_MARKETING_EXPORT_IDS,
    DROPPED_SALES_MARKETING_FILTER_IDS,
    MIGRATED_FILTER_IDS,
    NATIVE_EXPORT_IDS,
    NATIVE_FILTER_IDS,
    IdentitySourceReader,
    source_binary,
    source_options,
)


def normalized(value):
    if value is False or value is None:
        return None
    if isinstance(value, (Decimal, float)):
        decimal = Decimal(str(value)).normalize()
        return "0" if not decimal else format(decimal, "f")
    return value


def normalized_partner_field(field_name, value):
    if field_name in {"partner_latitude", "partner_longitude"} and not value:
        return "0"
    if field_name in {"supplier_rank", "customer_rank"} and value is None:
        return 0
    return normalized(value)


def digest(rows):
    return hashlib.sha256(
        json.dumps(
            rows,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            default=str,
        ).encode(),
    ).hexdigest()


source = IdentitySourceReader(source_options()).read()
run = env["usl.identity.restore.run"].sudo().search([], order="id desc", limit=1)
assert run and run.status == "passed", "Latest identity restoration did not pass."
assert run.statistics_json["source"] == source["counts"]
assert run.statistics_json["target"] == source["counts"]


def traced(model, rows):
    source_ids = [row["id"] for row in rows]
    records = (
        env[model]
        .sudo()
        .with_context(active_test=False)
        .search(
            [
                ("rebuild_source_model", "=", model),
                ("rebuild_source_id", "in", source_ids or [0]),
                ("rebuild_source_snapshot", "=", run.source_snapshot),
            ],
        )
    )
    result = {record.rebuild_source_id: record for record in records}
    assert len(result) == len(rows), f"{model} identity count differs"
    assert len(records) == len(result), f"{model} has duplicate source identities"
    return result


companies = traced("res.company", source["companies"])
industries = traced("res.partner.industry", source["industries"])
categories = traced("res.partner.category", source["categories"])
partners = traced("res.partner", source["partners"])
users = traced("res.users", source["users"])
banks = traced("res.partner.bank", source["banks"])

xmlids = {}
for row in source["xmlids"]:
    xmlids.setdefault((row["model"], row["res_id"]), []).append(row["xmlid"])


def external_identity(record):
    if not record:
        return None
    target_xmlids = set(record.get_external_id().values())
    return sorted(target_xmlids)[0] if target_xmlids else None


def expected_xmlid(model, source_id):
    candidates = xmlids.get((model, source_id), [])
    for candidate in candidates:
        if env.ref(candidate, raise_if_not_found=False):
            return candidate
    return None


def native_runtime_user_xmlid(source_id):
    runtime_xmlids = {
        "base.user_root",
        "base.public_user",
        "base.template_portal_user_id",
    }
    return next(
        (
            xmlid
            for xmlid in xmlids.get(("res.users", source_id), [])
            if xmlid in runtime_xmlids
        ),
        None,
    )


def actual_reference(record, model, source_id):
    expected = expected_xmlid(model, source_id)
    if not expected:
        return external_identity(record)
    return expected if env.ref(expected, raise_if_not_found=False) == record else None


source_categories = {}
for relation in source["partner_categories"]:
    source_categories.setdefault(relation["partner_id"], []).append(relation["category_id"])
source_user_companies = {}
for relation in source["user_companies"]:
    source_user_companies.setdefault(relation["user_id"], []).append(relation["company_id"])
group_equivalents = {
    "accountant.group_account_user": "account.group_account_user",
    "documents.group_documents_manager": "usl_documents.group_documents_manager",
    "documents.group_documents_system": "usl_documents.group_documents_manager",
}
missing_group_xmlids = set()
runtime_group_xmlids = {
    "base.group_everyone",
    "base.group_portal",
    "base.group_public",
    "base.group_user",
}
for relation in source["user_groups"]:
    if relation["xmlid"] in runtime_group_xmlids:
        continue
    target_xmlid = group_equivalents.get(relation["xmlid"], relation["xmlid"])
    group = env.ref(target_xmlid, raise_if_not_found=False)
    if group and group._name == "res.groups":
        assert group in users[relation["user_id"]].group_ids, (
            f"User {relation['user_id']} lacks mapped group {target_xmlid}"
        )
    else:
        missing_group_xmlids.add(
            relation["xmlid"] or f"source-group:{relation['group_id']}",
        )
assert sorted(missing_group_xmlids) == run.statistics_json[
    "deferred_user_group_xmlids"
]

partner_fields = (
    "name", "color", "ref", "vat", "company_registry", "website", "function",
    "type", "street", "street2", "zip", "city", "email", "phone", "comment",
    "partner_latitude", "partner_longitude", "active", "employee",
    "supplier_rank", "customer_rank", "message_bounce",
)
source_partner_rows = []
target_partner_rows = []
native_partner_ids = {
    row["partner_id"]
    for row in source["users"]
    if native_runtime_user_xmlid(row["id"])
}
partner_to_user = {row["partner_id"]: row["id"] for row in source["users"]}
for row in source["partners"]:
    partner = partners[row["id"]]
    if row["id"] in native_partner_ids:
        source_partner_rows.append({"id": row["id"], "native_runtime": True})
        target_partner_rows.append(
            {
                "id": row["id"],
                "native_runtime": partner
                == users[partner_to_user[row["id"]]].partner_id,
            },
        )
        continue
    source_partner_rows.append(
        {
            "id": row["id"],
            **{
                field_name: normalized_partner_field(field_name, row[field_name])
                for field_name in partner_fields
            },
            "company": row["company_id"],
            "parent": row["parent_id"],
            "user": row["user_id"],
            "industry": row["industry_id"],
            "country": expected_xmlid("res.country", row["country_id"]),
            "state": expected_xmlid("res.country.state", row["state_id"]),
            "categories": sorted(source_categories.get(row["id"], [])),
        },
    )
    target_partner_rows.append(
        {
            "id": row["id"],
            **{
                field_name: normalized_partner_field(
                    field_name,
                    partner[field_name],
                )
                for field_name in partner_fields
            },
            "company": partner.company_id.rebuild_source_id or None,
            "parent": partner.parent_id.rebuild_source_id or None,
            "user": partner.user_id.rebuild_source_id or None,
            "industry": partner.industry_id.rebuild_source_id or None,
            "country": actual_reference(
                partner.country_id,
                "res.country",
                row["country_id"],
            ),
            "state": actual_reference(
                partner.state_id,
                "res.country.state",
                row["state_id"],
            ),
            "categories": sorted(partner.category_id.mapped("rebuild_source_id")),
        },
    )

source_user_rows = []
target_user_rows = []
for row in source["users"]:
    user = users[row["id"]]
    native_xmlid = native_runtime_user_xmlid(row["id"])
    if native_xmlid:
        source_user_rows.append(
            {"id": row["id"], "native_xmlid": native_xmlid},
        )
        target_user_rows.append(
            {
                "id": row["id"],
                "native_xmlid": native_xmlid
                if env.ref(native_xmlid, raise_if_not_found=False) == user
                else None,
            },
        )
    else:
        is_source_manager = "base.user_admin" in set(
            xmlids.get(("res.users", row["id"]), []),
        )
        source_user_rows.append(
            {
                "id": row["id"],
                "login": user.login if is_source_manager else row["login"],
                "active": row["active"],
                "share": row["share"],
                "company": row["company_id"],
                "companies": sorted(source_user_companies.get(row["id"], [])),
                "multi_company_expenses": (
                    not row["share"]
                    and len(source_user_companies.get(row["id"], [])) > 1
                ),
                "partner": row["partner_id"],
                "signature": row["signature"],
            },
        )
        target_user_rows.append(
            {
                "id": row["id"],
                "login": user.login,
                "active": user.active,
                "share": user.share,
                "company": user.company_id.rebuild_source_id,
                "companies": sorted(user.company_ids.mapped("rebuild_source_id")),
                "multi_company_expenses": user.usl_expense_multi_company,
                "partner": user.partner_id.rebuild_source_id,
                "signature": user.signature,
            },
        )

source_bank_rows = []
target_bank_rows = []
bank_fields = (
    "sequence", "account_number", "clearing_number", "holder_name", "note",
    "active", "allow_out_payment", "bank_name", "bank_bic", "street", "street2",
    "zip", "city",
)
for row in source["banks"]:
    bank = banks[row["id"]]
    source_bank_rows.append(
        {
            "id": row["id"],
            **{field_name: normalized(row[field_name]) for field_name in bank_fields},
            "partner": row["partner_id"],
            "company": row["company_id"],
            "country": expected_xmlid("res.country", row["country_id"]),
            "state": expected_xmlid("res.country.state", row["state_id"]),
        },
    )
    target_bank_rows.append(
        {
            "id": row["id"],
            **{field_name: normalized(bank[field_name]) for field_name in bank_fields},
            "partner": bank.partner_id.rebuild_source_id,
            "company": bank.company_id.rebuild_source_id or None,
            "country": actual_reference(
                bank.country_id,
                "res.country",
                row["country_id"],
            ),
            "state": actual_reference(
                bank.state_id,
                "res.country.state",
                row["state_id"],
            ),
        },
    )

parity = {
    "company_partners": (
        digest(
            [
                (row["id"], row["partner_id"])
                for row in source["companies"]
            ],
        ),
        digest(
            [
                (
                    row["id"],
                    companies[row["id"]].partner_id.rebuild_source_id,
                )
                for row in source["companies"]
            ],
        ),
    ),
    "partners": (digest(source_partner_rows), digest(target_partner_rows)),
    "users": (digest(source_user_rows), digest(target_user_rows)),
    "banks": (digest(source_bank_rows), digest(target_bank_rows)),
    "industries": (
        digest([(row["id"], run._text(row["name"]), row["active"]) for row in source["industries"]]),
        digest([(source_id, record.name, record.active) for source_id, record in sorted(industries.items())]),
    ),
    "categories": (
        digest([(row["id"], run._text(row["name"]), row["color"], row["parent_id"], row["active"]) for row in source["categories"]]),
        digest([(source_id, record.name, record.color, record.parent_id.rebuild_source_id or None, record.active) for source_id, record in sorted(categories.items())]),
    ),
    "images": (
        digest(
            [
                (row["id"], row["res_id"], row["checksum"], row["file_size"])
                for row in source["images"]
            ],
        ),
        digest(
            [
                (
                    row["id"],
                    row["res_id"],
                    hashlib.sha1(
                        bytes(partners[row["res_id"]].image_1920),
                        usedforsecurity=False,
                    ).hexdigest(),
                    len(bytes(partners[row["res_id"]].image_1920)),
                )
                for row in source["images"]
            ],
        ),
    ),
}

for row in source["images"]:
    assert bytes(partners[row["res_id"]].image_1920) == source_binary(row)
for row in source["companies"]:
    assert companies[row["id"]].partner_id == partners[row["partner_id"]]
parity_examples = {}
for area, (source_rows, target_rows) in {
    "partners": (source_partner_rows, target_partner_rows),
    "users": (source_user_rows, target_user_rows),
    "banks": (source_bank_rows, target_bank_rows),
}.items():
    examples = []
    for source_row, target_row in zip(source_rows, target_rows, strict=True):
        differing_fields = sorted(
            field_name
            for field_name in set(source_row) | set(target_row)
            if source_row.get(field_name) != target_row.get(field_name)
        )
        if differing_fields:
            examples.append(
                {"source_id": source_row["id"], "fields": differing_fields},
            )
        if len(examples) == 12:
            break
    parity_examples[area] = examples
assert all(
    source_digest == target_digest
    for source_digest, target_digest in parity.values()
), {"digests": parity, "examples": parity_examples}

preference_dispositions = run.statistics_json["preference_dispositions"]
if preference_dispositions.get("status") == "deferred":
    assert not env["ir.filters"].sudo().search(
        [("name", "in", [
            row["name"]
            for row in source["filters"]
            if row["id"] in MIGRATED_FILTER_IDS
        ])],
    )
else:
    assert preference_dispositions["filters"]["migrated"] == sorted(
        MIGRATED_FILTER_IDS,
    )
    assert preference_dispositions["filters"]["native_recomputed"] == sorted(
        NATIVE_FILTER_IDS,
    )
    assert preference_dispositions["filters"][
        "deliberately_not_copied_sales_marketing"
    ] == sorted(DROPPED_SALES_MARKETING_FILTER_IDS)
    assert preference_dispositions["exports"]["native_recomputed"] == sorted(
        NATIVE_EXPORT_IDS,
    )
    assert preference_dispositions["exports"][
        "deliberately_not_copied_sales_marketing"
    ] == sorted(DROPPED_SALES_MARKETING_EXPORT_IDS)
    assert preference_dispositions["exports"][
        "deliberately_not_copied_ai_experiments"
    ] == sorted(DROPPED_AI_EXPORT_IDS)

    filter_users = {}
    for relation in source["filter_users"]:
        filter_users.setdefault(relation["filter_id"], []).append(
            users[relation["user_id"]].id,
        )
    action_xmlids = {
        row["res_id"]: row["xmlid"]
        for row in source["xmlids"]
        if row["model"] == "ir.actions.act_window"
    }
    target_filter_ids = preference_dispositions["filters"]["target_ids"]
    assert len(target_filter_ids) == len(MIGRATED_FILTER_IDS)
    for row, target_id in zip(
        [item for item in source["filters"] if item["id"] in MIGRATED_FILTER_IDS],
        target_filter_ids,
        strict=True,
    ):
        target = env["ir.filters"].sudo().browse(target_id).exists()
        assert target, f"Saved filter {row['id']} is missing"
        action_xmlid = action_xmlids.get(row["action_id"])
        action = (
            env.ref(action_xmlid, raise_if_not_found=False)
            if action_xmlid
            else False
        )
        if row["id"] == 14:
            action = env.ref(
                "rebuild_account_migration.action_rebuild_account_reconcile_bank_transactions",
            )
        assert target.name == row["name"]
        assert target.model_id == row["model_id"]
        assert target.domain == repr(run._translate_filter_domain(row))
        assert target.context == (row["context"] or "{}")
        assert target.sort == (row["sort"] or "[]")
        assert target.is_default == bool(row["is_default"])
        assert target.active == bool(row["active"])
        assert target.action_id.id == (action.id if action else False)
        assert sorted(target.user_ids.ids) == sorted(
            filter_users.get(row["id"], []),
        )

    home = preference_dispositions["home"]
    valentin = env["res.users"].sudo().search(
        [("login", "=", home["user_login"])],
        limit=1,
    )
    assert valentin, "Valentin is missing after Home preference restoration"
    assert valentin.action_id.id == env.ref("usl_home.action_usl_home").id
    settings = env["res.users.settings"].sudo()._find_or_create_for_user(valentin)
    assert settings.usl_home_layout == home["layout"]
    assert settings.usl_home_favorites_initialized
    home_favorites = env["usl.home.favorite"].sudo().search(
        [("user_id", "=", valentin.id)],
        order="sequence, id",
    )
    assert len(home_favorites) == home["favorite_count"]
    assert home_favorites.mapped("name") == home["favorite_names"]
    assert home["saved_filter_source_ids"] == [14, 6]
    assert home_favorites.filtered(
        lambda favorite: favorite.filter_id.id in target_filter_ids,
    ).mapped("filter_id").ids == [
        target_filter_ids[
            [
                item["id"]
                for item in source["filters"]
                if item["id"] in MIGRATED_FILTER_IDS
            ].index(source_id)
        ]
        for source_id in home["saved_filter_source_ids"]
    ]
    assert all(
        favorite.user_id == valentin
        for favorite in home_favorites
    )

# Credential-bearing source fields are intentionally absent from the reader and
# cannot enter the target. Authentication is re-enrolled through Pocket ID.
assert "password" not in source["users"][0]
assert "totp_secret" not in source["users"][0]

summary = {
    "counts": source["counts"],
    "parity_sha256": {key: value[0] for key, value in parity.items()},
    "credentials_copied": False,
    "mapped_user_group_memberships": run.statistics_json[
        "mapped_user_groups"
    ],
    "recomputed_runtime_user_group_memberships": run.statistics_json[
        "recomputed_runtime_user_groups"
    ],
    "deferred_user_group_xmlids": sorted(missing_group_xmlids),
    "recomputed_is_company_source_ids": sorted(
        row["id"]
        for row in source["partners"]
        if row["id"] not in native_partner_ids
        and partners[row["id"]].is_company != row["is_company"]
    ),
    "source_snapshot": run.source_snapshot,
    "preference_dispositions": preference_dispositions,
}
print(json.dumps(summary, indent=2, sort_keys=True))
