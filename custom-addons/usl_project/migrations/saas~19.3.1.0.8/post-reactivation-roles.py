def migrate(cr, version):
    cr.execute(
        """
        WITH ranked AS (
            SELECT project_task_type.id,
                   row_number() OVER (
                       PARTITION BY project_task_type.user_id
                       ORDER BY project_task_type.sequence, project_task_type.id
                   ) AS first_rank,
                   row_number() OVER (
                       PARTITION BY project_task_type.user_id
                       ORDER BY project_task_type.sequence DESC,
                                project_task_type.id DESC
                   ) AS last_rank
              FROM project_task_type
              JOIN res_users ON res_users.id = project_task_type.user_id
             WHERE project_task_type.user_id IS NOT NULL
               AND project_task_type.active
               AND res_users.active
               AND NOT res_users.share
               AND NOT project_task_type.fold
        )
        UPDATE project_task_type AS stage
           SET usl_reactivation_role = CASE
                   WHEN ranked.first_rank = 1 THEN 'inbox'
                   WHEN ranked.last_rank = 1 AND ranked.first_rank > 1 THEN 'later'
               END
          FROM ranked
         WHERE stage.id = ranked.id
           AND stage.usl_reactivation_role IS NULL
           AND (ranked.first_rank = 1 OR ranked.last_rank = 1)
        """
    )
