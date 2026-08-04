def migrate(cr, version):
    del version
    cr.execute(
        """
        ALTER TABLE usl_tese_payslip
            ADD COLUMN IF NOT EXISTS pay_period date
        """,
    )
    cr.execute(
        """
        UPDATE usl_tese_payslip
           SET pay_period = COALESCE(
               period_start,
               make_date(pay_year, pay_month, 1)
           )
         WHERE pay_period IS NULL
        """,
    )
    cr.execute(
        """
        ALTER TABLE usl_tese_payslip
            ALTER COLUMN pay_period SET NOT NULL
        """,
    )
    for constraint_name in (
        "usl_tese_payslip_period_employee_unique",
        "usl_tese_payslip_month_range",
        "usl_tese_payslip_year_range",
    ):
        cr.execute(
            f"""
            ALTER TABLE usl_tese_payslip
                DROP CONSTRAINT IF EXISTS {constraint_name}
            """,
        )
