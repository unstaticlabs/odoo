def migrate(cr, version):
    del version
    cr.execute(
        """
        UPDATE account_immediate_settlement
           SET payment_rate_application = 'legacy_bank_adjustment'
         WHERE mechanism = 'payment_rate'
           AND payment_rate_application IS NULL
        """,
    )
