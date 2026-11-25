"""Allow the existing reactivation roles on project stages."""


def migrate(cr, version):
    cr.execute(
        """
        ALTER TABLE project_task_type
        DROP CONSTRAINT IF EXISTS project_task_type_usl_reactivation_role_owner
        """
    )
