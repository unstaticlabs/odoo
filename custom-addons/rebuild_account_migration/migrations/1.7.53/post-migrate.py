def migrate(cr, version):
    del version
    cr.execute(
        """
        INSERT INTO rebuild_account_hygiene_dismissal (
            issue_id,
            company_id,
            dismissed_at,
            dismissed_by_id,
            related_record_count,
            target_model,
            target_res_ids_json,
            evidence_snapshot,
            create_uid,
            create_date,
            write_uid,
            write_date
        )
        SELECT
            issue.id,
            issue.company_id,
            issue.dismissed_at,
            issue.dismissed_by_id,
            CASE
                WHEN issue.target_res_ids_json IS NULL THEN
                    CASE WHEN issue.target_res_id IS NULL THEN 0 ELSE 1 END
                ELSE jsonb_array_length(issue.target_res_ids_json::jsonb)
            END,
            issue.target_model,
            issue.target_res_ids_json,
            issue.evidence,
            issue.dismissed_by_id,
            issue.dismissed_at,
            issue.dismissed_by_id,
            issue.dismissed_at
        FROM rebuild_account_hygiene_issue AS issue
        WHERE issue.status = 'dismissed'
          AND issue.dismissed_at IS NOT NULL
          AND issue.dismissed_by_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1
                FROM rebuild_account_hygiene_dismissal AS dismissal
               WHERE dismissal.issue_id = issue.id
          )
        """,
    )
