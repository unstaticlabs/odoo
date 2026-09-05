"""Classify and compare recovery controls without hiding release changes."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping


SCHEMA_V1 = "usl-control-manifest/v1"
SCHEMA = "usl-control-manifest/v2"

# These records express business history. A module upgrade may extend their
# schema, but it may not silently add, remove or rewrite the captured records.
ODOO_PRESERVATION_KEYS = frozenset(
    {
        "companies",
        "users",
        "employees",
        "agents",
        "moves",
        "move_lines",
        "posted_move_fingerprint",
        "posted_line_fingerprint",
        "partial_reconcile_fingerprint",
        "currency_rate_fingerprint",
        "attachments",
        "messages",
        "activities",
        "stored_attachments",
        "projects",
        "tasks",
        "expenses",
        "assets",
        "analytics",
        "taxes",
        "platform_sessions",
        "platform_payouts",
        "tese_payslips",
        "ledger_delta",
    },
)

ODOO_PRESERVATION_KEYS_V2 = ODOO_PRESERVATION_KEYS | frozenset(
    {
        "account_balance_fingerprint",
        "agent_authority_fingerprint",
        "company_identity_fingerprint",
        "journal_control_fingerprint",
        "lock_date_fingerprint",
        "oidc_identity_fingerprint",
        "user_authority_fingerprint",
    },
)

# These values are owned by the candidate release. They may legitimately
# change during an upgrade, but production must match what staging signed.
ODOO_RELEASE_KEYS = frozenset(
    {
        "acl_fingerprint",
        "record_rule_fingerprint",
    },
)

ODOO_RELEASE_KEYS_V2 = ODOO_RELEASE_KEYS | frozenset(
    {
        "cron_policy_fingerprint",
        "group_implication_fingerprint",
    },
)

# Cron activation is target-specific: staging is deliberately neutralized while
# production follows deploy/production.cron-policy.json. The fingerprint below
# therefore seals cron identity and scheduling, but not the active flag.

# Pending work may drain during maintenance. It may not grow while all writers
# are quiesced. Failed work and cron failures are rejected by smoke admission.
ODOO_QUEUE_KEYS = frozenset(
    {
        "queued_mail",
        "failed_mail",
        "pending_documents",
        "failed_documents",
        "bank_pending",
        "bank_failed",
        "payment_pending",
        "payment_failed",
        "sign_archive_pending",
        "sign_archive_failed",
        "cron_failures",
    },
)
ODOO_QUEUE_KEYS_V2 = ODOO_QUEUE_KEYS | frozenset({"cron_lag"})

PAPERLESS_PRESERVATION_KEYS = frozenset(
    {
        "documents",
        "with_ocr",
        "missing_original_name",
    },
)

PAPERLESS_PRESERVATION_KEYS_V2 = PAPERLESS_PRESERVATION_KEYS | frozenset(
    {
        "document_identity_fingerprint",
        "permission_fingerprint",
        "tag_link_fingerprint",
        "trash_count",
    },
)


ODOO_CONTROL_SQL = r"""
SELECT json_build_object(
  'companies', (SELECT count(*) FROM res_company),
  'users', (SELECT count(*) FROM res_users),
  'employees', (SELECT count(*) FROM hr_employee),
  'agents', (SELECT count(*) FROM usl_agent),
  'company_identity_fingerprint', (SELECT md5(coalesce(string_agg(concat_ws('|', id, parent_id, active, currency_id), E'\n' ORDER BY id), '')) FROM res_company),
  'user_authority_fingerprint', (SELECT md5(concat(
    (SELECT coalesce(string_agg(concat_ws('|', id, active, company_id), E'\n' ORDER BY id), '') FROM res_users), E'\n--companies--\n',
    (SELECT coalesce(string_agg(concat_ws('|', user_id, cid), E'\n' ORDER BY user_id, cid), '') FROM res_company_users_rel), E'\n--groups--\n',
    (SELECT coalesce(string_agg(concat_ws('|', uid, gid), E'\n' ORDER BY uid, gid), '') FROM res_groups_users_rel)
  ))),
  'oidc_identity_fingerprint', (SELECT md5(coalesce(string_agg(concat_ws('|', id, provider_id, user_id, issuer, subject_fingerprint, active, link_method), E'\n' ORDER BY id), '')) FROM usl_oidc_identity),
  'agent_authority_fingerprint', (SELECT md5(concat(
    (SELECT coalesce(string_agg(concat_ws('|', id, owner_id, user_id, company_id, state, access_mode), E'\n' ORDER BY id), '') FROM usl_agent), E'\n--companies--\n',
    (SELECT coalesce(string_agg(concat_ws('|', agent_id, company_id), E'\n' ORDER BY agent_id, company_id), '') FROM usl_agent_company_rel), E'\n--delegated--\n',
    (SELECT coalesce(string_agg(concat_ws('|', agent_id, group_id), E'\n' ORDER BY agent_id, group_id), '') FROM usl_agent_delegated_group_rel), E'\n--read-only--\n',
    (SELECT coalesce(string_agg(concat_ws('|', agent_id, group_id), E'\n' ORDER BY agent_id, group_id), '') FROM usl_agent_read_only_group_rel)
  ))),
  'moves', (SELECT count(*) FROM account_move),
  'move_lines', (SELECT count(*) FROM account_move_line),
  'posted_move_fingerprint', (SELECT md5(coalesce(string_agg(concat_ws('|', id, company_id, journal_id, name, date, state, amount_total, amount_residual, payment_state), E'\n' ORDER BY id), '')) FROM account_move WHERE state = 'posted'),
  'posted_line_fingerprint', (SELECT md5(coalesce(string_agg(concat_ws('|', id, move_id, company_id, account_id, debit, credit, balance, currency_id, amount_currency, reconciled), E'\n' ORDER BY id), '')) FROM account_move_line WHERE parent_state = 'posted'),
  'partial_reconcile_fingerprint', (SELECT md5(coalesce(string_agg(concat_ws('|', id, debit_move_id, credit_move_id, amount, debit_amount_currency, credit_amount_currency), E'\n' ORDER BY id), '')) FROM account_partial_reconcile),
  'account_balance_fingerprint', (SELECT md5(coalesce(string_agg(concat_ws('|', company_id, account_id, currency_id, debit, credit, balance, amount_currency), E'\n' ORDER BY company_id, account_id, currency_id NULLS FIRST), '')) FROM (SELECT company_id, account_id, currency_id, sum(debit) debit, sum(credit) credit, sum(balance) balance, sum(amount_currency) amount_currency FROM account_move_line WHERE parent_state = 'posted' GROUP BY company_id, account_id, currency_id) balances),
  'journal_control_fingerprint', (SELECT md5(coalesce(string_agg(concat_ws('|', id, company_id, code, type, active, sequence, refund_sequence, restrict_mode_hash_table, invoice_reference_type, invoice_reference_model, sequence_override_regex), E'\n' ORDER BY id), '')) FROM account_journal),
  'lock_date_fingerprint', (SELECT md5(coalesce(string_agg(concat_ws('|', id, fiscalyear_lock_date, tax_lock_date, sale_lock_date, purchase_lock_date, hard_lock_date), E'\n' ORDER BY id), '')) FROM res_company),
  'acl_fingerprint', (SELECT md5(coalesce(string_agg(concat_ws('|', id, model_id, group_id, perm_read, perm_write, perm_create, perm_unlink, active), E'\n' ORDER BY id), '')) FROM ir_model_access),
  'record_rule_fingerprint', (SELECT md5(coalesce(string_agg(concat_ws('|', id, model_id, domain_force, perm_read, perm_write, perm_create, perm_unlink, active), E'\n' ORDER BY id), '')) FROM ir_rule),
  'group_implication_fingerprint', (SELECT md5(coalesce(string_agg(concat_ws('|', gid, hid), E'\n' ORDER BY gid, hid), '')) FROM res_groups_implied_rel),
  'cron_policy_fingerprint', (SELECT md5(coalesce(string_agg(concat_ws('|', data.module, data.name, cron.interval_number, cron.interval_type, cron.priority, cron.user_id, cron.ir_actions_server_id), E'\n' ORDER BY data.module, data.name), '')) FROM ir_cron cron LEFT JOIN ir_model_data data ON data.model = 'ir.cron' AND data.res_id = cron.id),
  'currency_rate_fingerprint', (SELECT md5(coalesce(string_agg(concat_ws('|', id, company_id, currency_id, name, rate), E'\n' ORDER BY id), '')) FROM res_currency_rate),
  'attachments', (SELECT count(*) FROM ir_attachment),
  'messages', (SELECT count(*) FROM mail_message WHERE model IS NULL OR model NOT IN ('ir.cron', 'ir.actions.server')),
  'activities', (SELECT count(*) FROM mail_activity),
  'stored_attachments', (SELECT count(DISTINCT store_fname) FROM ir_attachment WHERE store_fname IS NOT NULL),
  'projects', (SELECT count(*) FROM project_project),
  'tasks', (SELECT count(*) FROM project_task),
  'expenses', (SELECT count(*) FROM hr_expense),
  'assets', (SELECT count(*) FROM account_asset),
  'analytics', (SELECT count(*) FROM account_analytic_line),
  'taxes', (SELECT count(*) FROM account_tax),
  'platform_sessions', (SELECT count(*) FROM usl_platform_billing_session),
  'platform_payouts', (SELECT count(*) FROM usl_platform_billing_payout),
  'tese_payslips', (SELECT count(*) FROM usl_tese_payslip),
  'ledger_delta', (SELECT coalesce(sum(debit-credit), 0) FROM account_move_line),
  'queued_mail', (SELECT count(*) FROM mail_mail WHERE state = 'outgoing'),
  'failed_mail', (SELECT count(*) FROM mail_mail WHERE state = 'exception'),
  'pending_documents', (SELECT count(*) FROM usl_document_operation WHERE state IN ('pending','uploading','processing','duplicate')),
  'failed_documents', (SELECT count(*) FROM usl_document_operation WHERE state = 'failed'),
  'bank_pending', (SELECT count(*) FROM account_bank_ingestion WHERE state IN ('received','processing')),
  'bank_failed', (SELECT count(*) FROM account_bank_ingestion WHERE state = 'failed'),
  'payment_pending', (SELECT count(*) FROM payment_transaction WHERE state IN ('draft','pending','authorized')),
  'payment_failed', (SELECT count(*) FROM payment_transaction WHERE state = 'error'),
  'sign_archive_pending', (SELECT count(*) FROM sign_oca_request WHERE archive_status IN ('pending','processing')),
  'sign_archive_failed', (SELECT count(*) FROM sign_oca_request WHERE archive_status = 'failed'),
  'cron_failures', (SELECT coalesce(sum(failure_count), 0) FROM ir_cron WHERE active),
  'cron_lag', (SELECT count(*) FROM ir_cron WHERE active AND (nextcall IS NULL OR nextcall < now() - interval '2 minutes' OR (interval_type IN ('minutes', 'hours') AND interval_number * CASE interval_type WHEN 'minutes' THEN 60 ELSE 3600 END <= 3600 AND (lastcall IS NULL OR lastcall < now() - make_interval(secs => interval_number * CASE interval_type WHEN 'minutes' THEN 60 ELSE 3600 END * 2 + 120)))))
);""".strip()


PAPERLESS_CONTROL_SQL = r"""
SELECT json_build_object(
  'documents', count(*),
  'with_ocr', count(*) FILTER (WHERE coalesce(content, '') <> ''),
  'missing_original_name', count(*) FILTER (WHERE coalesce(filename, '') = ''),
  'trash_count', count(*) FILTER (WHERE deleted_at IS NOT NULL),
  'document_identity_fingerprint', (SELECT md5(coalesce(string_agg(concat_ws('|', id, checksum, archive_checksum, page_count, filename, archive_filename, original_filename, archive_serial_number, correspondent_id, document_type_id, storage_path_id, owner_id, deleted_at, restored_at, root_document_id, version_index), E'\n' ORDER BY id), '')) FROM documents_document),
  'tag_link_fingerprint', (SELECT md5(coalesce(string_agg(concat_ws('|', document_id, tag_id), E'\n' ORDER BY document_id, tag_id), '')) FROM documents_document_tags),
  'permission_fingerprint', md5(concat(
    (SELECT coalesce(string_agg(concat_ws('|', user_id, group_id), E'\n' ORDER BY user_id, group_id), '') FROM auth_user_groups), E'\n--direct--\n',
    (SELECT coalesce(string_agg(concat_ws('|', user_id, permission_id), E'\n' ORDER BY user_id, permission_id), '') FROM auth_user_user_permissions), E'\n--user-object--\n',
    (SELECT coalesce(string_agg(concat_ws('|', object_pk, content_type_id, user_id, permission_id), E'\n' ORDER BY content_type_id, object_pk, user_id, permission_id), '') FROM guardian_userobjectpermission), E'\n--group-object--\n',
    (SELECT coalesce(string_agg(concat_ws('|', object_pk, content_type_id, group_id, permission_id), E'\n' ORDER BY content_type_id, object_pk, group_id, permission_id), '') FROM guardian_groupobjectpermission)
  ))
) FROM documents_document;""".strip()

PENDING_QUEUE_KEYS = frozenset(
    {
        "queued_mail",
        "pending_documents",
        "bank_pending",
        "payment_pending",
        "sign_archive_pending",
    },
)

FAILED_QUEUE_KEYS = frozenset(
    {
        "failed_mail",
        "failed_documents",
        "bank_failed",
        "payment_failed",
        "sign_archive_failed",
        "cron_failures",
    },
)
# Scheduler lateness is advisory: deployment intentionally pauses workers.
# Actual failed jobs remain blocking, independently of schedule timing.
FAILED_QUEUE_KEYS_V2 = FAILED_QUEUE_KEYS


class ControlManifestError(ValueError):
    """A control set is incomplete, unknown, or indicates data drift."""


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode(),
    ).hexdigest()


def _exact_section(
    values: Mapping[str, Any],
    expected: frozenset[str],
    label: str,
) -> dict[str, Any]:
    actual = set(values)
    if actual != expected:
        missing = sorted(expected - actual)
        unknown = sorted(actual - expected)
        raise ControlManifestError(
            f"{label} control fields differ: missing={missing}, unknown={unknown}",
        )
    return {key: values[key] for key in sorted(expected)}


def classify(controls: object) -> dict[str, Any]:
    """Return fail-closed business, release, and queue control sections."""
    if not isinstance(controls, dict) or set(controls) != {"odoo", "paperless"}:
        raise ControlManifestError("control roots must be exactly Odoo and Paperless")
    odoo = controls["odoo"]
    paperless = controls["paperless"]
    if not isinstance(odoo, dict) or not isinstance(paperless, dict):
        raise ControlManifestError("control roots must be objects")
    v1_odoo = ODOO_PRESERVATION_KEYS | ODOO_RELEASE_KEYS | ODOO_QUEUE_KEYS
    v2_odoo = ODOO_PRESERVATION_KEYS_V2 | ODOO_RELEASE_KEYS_V2 | ODOO_QUEUE_KEYS_V2
    if set(odoo) == v2_odoo:
        _exact_section(paperless, PAPERLESS_PRESERVATION_KEYS_V2, "Paperless")
        schema = SCHEMA
        preservation_keys = ODOO_PRESERVATION_KEYS_V2
        release_keys = ODOO_RELEASE_KEYS_V2
        paperless_keys = PAPERLESS_PRESERVATION_KEYS_V2
    elif set(odoo) == v1_odoo:
        _exact_section(paperless, PAPERLESS_PRESERVATION_KEYS, "Paperless")
        schema = SCHEMA_V1
        preservation_keys = ODOO_PRESERVATION_KEYS
        release_keys = ODOO_RELEASE_KEYS
        paperless_keys = PAPERLESS_PRESERVATION_KEYS
    else:
        _exact_section(odoo, v2_odoo, "Odoo")
        _exact_section(paperless, PAPERLESS_PRESERVATION_KEYS_V2, "Paperless")
        raise AssertionError("unreachable")
    paperless_values = _exact_section(paperless, paperless_keys, "Paperless")
    return {
        "schema": schema,
        "preservation": {
            "odoo": {key: odoo[key] for key in sorted(preservation_keys)},
            "paperless": paperless_values,
        },
        "release": {
            "odoo": {key: odoo[key] for key in sorted(release_keys)},
        },
        "queues": {
            "odoo": {
                key: odoo[key]
                for key in sorted(
                    ODOO_QUEUE_KEYS_V2 if schema == SCHEMA else ODOO_QUEUE_KEYS
                )
            },
        },
    }


def release_digest(controls: object) -> str:
    return _digest(classify(controls)["release"])


def validate_restore(
    before: object,
    after: object,
    *,
    expected_release_sha256: str | None = None,
    require_unchanged_release: bool = False,
) -> dict[str, Any]:
    """Validate preserved data, queue monotonicity, and release-owned policy."""
    baseline = classify(before)
    candidate = classify(after)
    if baseline["schema"] == SCHEMA and candidate["schema"] == SCHEMA_V1:
        raise ControlManifestError("restored control manifest regressed from v2 to v1")
    comparable_candidate = {
        root: {
            key: candidate["preservation"][root][key]
            for key in baseline["preservation"][root]
        }
        for root in baseline["preservation"]
    }
    if baseline["preservation"] != comparable_candidate:
        differences = {
            f"{root}.{key}": {"before": value, "after": comparable_candidate[root][key]}
            for root, values in baseline["preservation"].items()
            for key, value in values.items()
            if value != comparable_candidate[root][key]
        }
        raise ControlManifestError(
            "restored business controls differ from the source cohort: "
            + json.dumps(differences, sort_keys=True)
        )

    baseline_queues = baseline["queues"]["odoo"]
    candidate_queues = candidate["queues"]["odoo"]
    regressions = {
        key: {"before": baseline_queues[key], "after": candidate_queues[key]}
        for key in sorted(PENDING_QUEUE_KEYS)
        if candidate_queues[key] > baseline_queues[key]
    }
    if regressions:
        raise ControlManifestError(
            "pending queues grew while writers were quiesced: "
            + json.dumps(regressions, sort_keys=True),
        )
    failed_keys = FAILED_QUEUE_KEYS_V2 if candidate["schema"] == SCHEMA else FAILED_QUEUE_KEYS
    failed = {
        key: candidate_queues[key]
        for key in sorted(failed_keys)
        if candidate_queues[key]
    }
    if failed:
        raise ControlManifestError(
            "restored runtime has failed queues or crons: "
            + json.dumps(failed, sort_keys=True),
        )

    candidate_release_sha256 = _digest(candidate["release"])
    if require_unchanged_release:
        baseline_release_sha256 = _digest(baseline["release"])
        if candidate_release_sha256 != baseline_release_sha256:
            raise ControlManifestError("same-release restore changed release-owned controls")
    if expected_release_sha256 is not None:
        if candidate_release_sha256 != expected_release_sha256:
            raise ControlManifestError(
                "production release controls differ from staging-qualified evidence",
            )

    return {
        "schema": "usl-control-validation/v1",
        "control_schema": candidate["schema"],
        "preservation_sha256": _digest(candidate["preservation"]),
        "release_sha256": candidate_release_sha256,
        "queues": candidate["queues"],
        "status": "passed",
    }


# Cross-environment qualification uses model/XML identities, never database row IDs.
RELEASE_DEFINITIONS_SQL = r"""
WITH xml AS (SELECT model,res_id,min(module||'.'||name) AS xmlid FROM ir_model_data WHERE module NOT LIKE '\_\_%' GROUP BY model,res_id), groups AS (SELECT g.id,coalesce(x.xmlid,'name:'||g.name::text) AS identity FROM res_groups g LEFT JOIN xml x ON x.model='res.groups' AND x.res_id=g.id)
SELECT json_build_object(
'acl',(SELECT json_agg(json_build_object('identity',coalesce(x.xmlid,'name:'||a.name),'model',m.model,'group',g.identity,'read',a.perm_read,'write',a.perm_write,'create',a.perm_create,'unlink',a.perm_unlink,'active',a.active) ORDER BY coalesce(x.xmlid,'name:'||a.name)) FROM ir_model_access a JOIN ir_model m ON m.id=a.model_id LEFT JOIN groups g ON g.id=a.group_id LEFT JOIN xml x ON x.model='ir.model.access' AND x.res_id=a.id),
'rules',(SELECT json_agg(json_build_object('identity',coalesce(x.xmlid,'name:'||r.name),'model',m.model,'domain',r.domain_force,'groups',(SELECT json_agg(g.identity ORDER BY g.identity) FROM rule_group_rel rel JOIN groups g ON g.id=rel.group_id WHERE rel.rule_group_id=r.id),'read',r.perm_read,'write',r.perm_write,'create',r.perm_create,'unlink',r.perm_unlink,'active',r.active) ORDER BY coalesce(x.xmlid,'name:'||r.name)) FROM ir_rule r JOIN ir_model m ON m.id=r.model_id LEFT JOIN xml x ON x.model='ir.rule' AND x.res_id=r.id),
'crons',(SELECT json_agg(json_build_object('identity',x.xmlid,'interval_number',c.interval_number,'interval_type',c.interval_type,'priority',c.priority,'user',coalesce(u.xmlid,'login:'||users.login),'action',a.xmlid) ORDER BY x.xmlid) FROM ir_cron c LEFT JOIN xml x ON x.model='ir.cron' AND x.res_id=c.id LEFT JOIN xml a ON a.model='ir.actions.server' AND a.res_id=c.ir_actions_server_id LEFT JOIN xml u ON u.model='res.users' AND u.res_id=c.user_id LEFT JOIN res_users users ON users.id=c.user_id),
'groups',(SELECT json_agg(json_build_array(g.identity,h.identity) ORDER BY g.identity,h.identity) FROM res_groups_implied_rel rel JOIN groups g ON g.id=rel.gid JOIN groups h ON h.id=rel.hid)
);
"""


def release_definitions_digest(value: object) -> str:
    """Hash permission and scheduled-action definitions independent of row order."""
    if not isinstance(value, dict) or set(value) != {"acl", "rules", "crons", "groups"}:
        raise ControlManifestError("release definition sections differ")
    sections = {}
    for name, rows in value.items():
        if rows is None:
            rows = []
        if not isinstance(rows, list):
            raise ControlManifestError("release definitions must be lists")
        sections[name] = sorted(
            rows, key=lambda row: json.dumps(row, sort_keys=True, separators=(",", ":")),
        )
    return _digest(sections)
