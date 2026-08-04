def migrate(cr, _version):
    cr.execute(
        """
        ALTER TABLE usl_platform_billing_payout
            ALTER COLUMN platform_id DROP NOT NULL,
            ALTER COLUMN platform_currency_id DROP NOT NULL,
            ALTER COLUMN commission_rate_snapshot DROP NOT NULL
        """,
    )
