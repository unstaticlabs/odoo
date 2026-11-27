"""The default task stage now receives reminders on every board."""


def migrate(cr, version):
    cr.execute("""
        UPDATE project_task_type
           SET usl_reactivation_role = NULL
         WHERE usl_reactivation_role = 'inbox'
    """)
