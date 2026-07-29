def migrate(cr, version):
    del version
    cr.execute(
        """
        UPDATE res_company
           SET rebuild_first_fiscalyear_end = DATE '2025-09-30'
         WHERE rebuild_first_fiscalyear_start = DATE '2024-01-10'
           AND rebuild_first_fiscalyear_end IS NULL
           AND fiscalyear_last_day = 30
           AND fiscalyear_last_month = '9'
        """
    )
