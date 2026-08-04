def migrate(cr, version):
    del version
    cr.execute(
        """
        ALTER TABLE usl_tese_payslip
            DROP COLUMN IF EXISTS pay_month,
            DROP COLUMN IF EXISTS pay_year
        """,
    )
