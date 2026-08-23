def migrate(cr, version):
    replacements = {
        "USL benchmark 2024-01-10 to 2025-09-30": (
            "Fiscal year 2024-01-10 to 2025-09-30"
        ),
        "USL current from 2025-10-01": "Fiscal year from 2025-10-01",
        "USL Media full posted replay": "All posted accounting",
        "Other imported posted replay": "Other posted accounting",
    }
    for old_value, new_value in replacements.items():
        cr.execute(
            """
            UPDATE rebuild_account_assurance_decision
               SET period_key = %s
             WHERE period_key = %s
            """,
            [new_value, old_value],
        )
        cr.execute(
            """
            UPDATE rebuild_account_external_report_value
               SET period_key = %s
             WHERE period_key = %s
            """,
            [new_value, old_value],
        )
