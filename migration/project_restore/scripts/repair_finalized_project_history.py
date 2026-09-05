# ruff: noqa: F821, T201

"""Repair exact Project stage identities and duration clocks after finalization.

This migration-only repair is intentionally narrow and idempotent. It reads
the frozen Online source through a read-only PostgreSQL connection, updates
only Project workflow-stage references and native duration ledgers, and
removes only unreferenced duplicate stages created by the old importer.
"""

import json
import os

import psycopg2
import psycopg2.extras


if os.environ.get("USL_TRANSITION_WRITERS_QUIESCED") != "1":
    raise RuntimeError("Project history repair requires quiesced transition writers.")

source_options = {
    "host": os.environ.get("PROJECT_SOURCE_DB_HOST", "accounting-source-db"),
    "port": int(os.environ.get("PROJECT_SOURCE_DB_PORT", "5432")),
    "user": os.environ.get("PROJECT_SOURCE_DB_USER", "odoo"),
    "password": os.environ.get("PROJECT_SOURCE_DB_PASSWORD", "odoo"),
    "dbname": os.environ.get(
        "PROJECT_SOURCE_DATABASE",
        "odoo_online_source_saas_19_3",
    ),
    "connect_timeout": 10,
    "options": "-c default_transaction_read_only=on",
}

with psycopg2.connect(**source_options) as source_connection:
    with source_connection.cursor(
        cursor_factory=psycopg2.extras.RealDictCursor,
    ) as source_cursor:
        source_cursor.execute(
            """
                SELECT id, sequence, company_id, color, name, active, fold,
                       rotting_threshold_days
                  FROM project_project_stage
                 ORDER BY id
            """,
        )
        source_stages = [dict(row) for row in source_cursor.fetchall()]
        source_cursor.execute(
            """
                SELECT id, stage_id, duration_tracking
                  FROM project_project
                 ORDER BY id
            """,
        )
        source_projects = [dict(row) for row in source_cursor.fetchall()]

if not source_stages or not source_projects:
    raise RuntimeError("Frozen source Project stage history is unexpectedly empty.")

source_stage_ids = {row["id"] for row in source_stages}
source_project_ids = {row["id"] for row in source_projects}
for row in source_projects:
    ledger = row["duration_tracking"]
    if not isinstance(ledger, dict):
        raise RuntimeError(f"Source Project {row['id']} has an invalid duration ledger.")
    if ledger.get("s") != (row["stage_id"] or 0):
        raise RuntimeError(
            f"Source Project {row['id']} duration stage does not match stage_id.",
        )
    if row["stage_id"] not in source_stage_ids:
        raise RuntimeError(f"Source Project {row['id']} has an unknown stage.")

env.cr.execute("LOCK TABLE project_project, project_project_stage IN EXCLUSIVE MODE")
env.cr.execute("SELECT id FROM project_project ORDER BY id")
target_project_ids = {row[0] for row in env.cr.fetchall()}
if target_project_ids != source_project_ids:
    raise RuntimeError(
        "Target Project identities differ from the frozen source: "
        f"missing={sorted(source_project_ids - target_project_ids)!r}, "
        f"extra={sorted(target_project_ids - source_project_ids)!r}.",
    )

env.cr.execute(
    """
        SELECT id, sequence, company_id, color, name, active, fold,
               rotting_threshold_days
          FROM project_project_stage
         ORDER BY id
    """,
)
target_stages = {
    row[0]: {
        "id": row[0],
        "sequence": row[1],
        "company_id": row[2],
        "color": row[3],
        "name": row[4],
        "active": row[5],
        "fold": row[6],
        "rotting_threshold_days": row[7],
    }
    for row in env.cr.fetchall()
}


def stage_signature(row):
    return (
        row["sequence"],
        row["company_id"],
        row["color"] or 0,
        json.dumps(row["name"] or {}, sort_keys=True, ensure_ascii=False),
        bool(row["active"]),
        bool(row["fold"]),
        row["rotting_threshold_days"] or 0,
    )


source_signatures = {stage_signature(row) for row in source_stages}
for row in source_stages:
    target = target_stages.get(row["id"])
    if not target or stage_signature(target) != stage_signature(row):
        raise RuntimeError(
            f"Exact target Project stage ID {row['id']} is absent or incompatible.",
        )

updated_projects = 0
for row in source_projects:
    env.cr.execute(
        """
            UPDATE project_project
               SET stage_id = %s,
                   duration_tracking = %s
             WHERE id = %s
               AND (
                    stage_id IS DISTINCT FROM %s
                    OR duration_tracking IS DISTINCT FROM %s
               )
        """,
        (
            row["stage_id"],
            psycopg2.extras.Json(row["duration_tracking"]),
            row["id"],
            row["stage_id"],
            psycopg2.extras.Json(row["duration_tracking"]),
        ),
    )
    updated_projects += env.cr.rowcount

duplicate_ids = []
for stage_id, row in target_stages.items():
    if stage_id in source_stage_ids or stage_signature(row) not in source_signatures:
        continue
    env.cr.execute(
        "SELECT count(*) FROM project_project WHERE stage_id = %s",
        (stage_id,),
    )
    project_references = env.cr.fetchone()[0]
    env.cr.execute(
        """
            SELECT count(*)
              FROM ir_model_data
             WHERE model = 'project.project.stage' AND res_id = %s
        """,
        (stage_id,),
    )
    external_ids = env.cr.fetchone()[0]
    if project_references or external_ids:
        raise RuntimeError(
            f"Duplicate Project stage {stage_id} still has protected references.",
        )
    duplicate_ids.append(stage_id)

if duplicate_ids:
    env.cr.execute(
        "DELETE FROM project_project_stage WHERE id = ANY(%s)",
        (duplicate_ids,),
    )

env.cr.execute(
    """
        SELECT setval(
            pg_get_serial_sequence('project_project_stage', 'id'),
            COALESCE(max(id), 0) + 1,
            FALSE
        )
          FROM project_project_stage
    """,
)

for row in source_projects:
    env.cr.execute(
        """
            SELECT stage_id, duration_tracking
              FROM project_project
             WHERE id = %s
        """,
        (row["id"],),
    )
    stage_id, ledger = env.cr.fetchone()
    if stage_id != row["stage_id"] or ledger != row["duration_tracking"]:
        raise RuntimeError(f"Project {row['id']} history repair did not persist.")

env.cr.commit()
print(
    json.dumps(
        {
            "schema": "usl-project-history-repair-v1",
            "source_database": source_options["dbname"],
            "projects_verified": len(source_projects),
            "project_stages_verified": len(source_stages),
            "projects_updated": updated_projects,
            "duplicate_stages_removed": sorted(duplicate_ids),
            "status": "passed",
        },
        sort_keys=True,
    ),
)
