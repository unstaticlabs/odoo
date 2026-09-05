"""Prove existing records survive upgrades while allowing newly created records."""
from __future__ import annotations
import hashlib
import json
import re

TABLES = {"ir_attachment": "id", "mail_message": "id", "project_project": "id", "res_groups_users_rel": "gid"}
SCHEMA = "usl-upgrade-preservation/v1"


def scope_sql() -> str:
    sections = []
    for table, key in TABLES.items():
        sections.append(f"'{table}', json_build_object('maximum', (SELECT coalesce(max({key}), 0) FROM public.{table}), 'columns', (SELECT json_agg(column_name ORDER BY column_name) FROM information_schema.columns WHERE table_schema='public' AND table_name='{table}' AND column_name NOT IN ('write_date','write_uid')))")
    return "SELECT json_build_object(" + ",".join(sections) + ");"


def validate_scope(scope: object) -> dict:
    if not isinstance(scope, dict) or set(scope) != set(TABLES):
        raise ValueError("upgrade preservation scope tables differ")
    for table, item in scope.items():
        if not isinstance(item, dict) or set(item) != {"maximum", "columns"}:
            raise ValueError("upgrade preservation scope fields differ")
        maximum, columns = item['maximum'], item['columns']
        if type(maximum) is not int or maximum < 0:
            raise ValueError("upgrade preservation boundary is invalid")
        if not isinstance(columns, list) or not columns or not all(isinstance(c, str) and re.fullmatch(r'[a-z_][a-z0-9_]*', c) for c in columns) or columns != sorted(set(columns)) or TABLES[table] not in columns:
            raise ValueError("upgrade preservation columns are invalid")
    return scope


def fingerprint_sql(scope: dict) -> str:
    validate_scope(scope)
    sections = []
    for table, key in TABLES.items():
        item = scope[table]
        columns = ','.join("'"+c+"'" for c in item['columns'])
        # Preserve the original column set: adding a column is a schema change,
        # not a rewrite of the existing business values.
        row = f"(SELECT jsonb_object_agg(c.key, c.value ORDER BY c.key) FROM jsonb_each(to_jsonb(r)) c WHERE c.key = ANY(ARRAY[{columns}]))"
        order = 'r.uid,r.gid' if table == 'res_groups_users_rel' else 'r.id'
        row_hash = f"encode(sha256(convert_to(({row})::text, 'UTF8')), 'hex')"
        sections.append(f"'{table}', (SELECT json_build_object('count', count(*), 'sha256', encode(sha256(convert_to(coalesce(string_agg({row_hash}, E'\\n' ORDER BY {order}), ''), 'UTF8')), 'hex')) FROM public.{table} r WHERE {key} <= {item['maximum']})")
    return "SELECT json_build_object(" + ','.join(sections) + ");"


def scoped_controls_sql(sql: str, scope: dict) -> str:
    validate_scope(scope)
    ctes = [f"{table} AS (SELECT * FROM public.{table} WHERE {key} <= {scope[table]['maximum']})" for table, key in TABLES.items()]
    return 'WITH ' + ', '.join(ctes) + '\n' + sql


def validate_fingerprints(value: object) -> dict:
    if not isinstance(value, dict) or set(value) != set(TABLES):
        raise ValueError("upgrade preservation fingerprint tables differ")
    for item in value.values():
        if (
            not isinstance(item, dict)
            or set(item) != {"count", "sha256"}
            or type(item["count"]) is not int
            or item["count"] < 0
            or not isinstance(item["sha256"], str)
            or re.fullmatch(r"[0-9a-f]{64}", item["sha256"]) is None
        ):
            raise ValueError("upgrade preservation fingerprint is invalid")
    return value


def capture(execute) -> dict:
    scope = validate_scope(json.loads(execute(scope_sql())))
    fingerprints = validate_fingerprints(json.loads(execute(fingerprint_sql(scope))))
    return {'schema': SCHEMA, 'scope': scope, 'fingerprints': fingerprints}


def verify(before: dict, execute) -> dict:
    if not isinstance(before, dict) or set(before) != {'schema','scope','fingerprints'} or before['schema'] != SCHEMA:
        raise ValueError('upgrade preservation evidence fields differ')
    scope = validate_scope(before['scope'])
    # A removed column is not silently converted to NULL by the projection.
    current = validate_scope(json.loads(execute(scope_sql())))
    for table in TABLES:
        if not set(scope[table]['columns']) <= set(current[table]['columns']):
            raise ValueError('upgrade removed captured columns: ' + table)
    validate_fingerprints(before['fingerprints'])
    after = validate_fingerprints(json.loads(execute(fingerprint_sql(scope))))
    changed = [table for table in TABLES if before['fingerprints'][table] != after[table]]
    if changed:
        raise ValueError('upgrade changed or removed existing records: ' + ', '.join(changed))
    digest = hashlib.sha256(json.dumps(before, sort_keys=True, separators=(',',':')).encode()).hexdigest()
    return {'schema': SCHEMA, 'status': 'preserved', 'baseline_sha256': digest, 'scope': scope, 'fingerprints': after}
