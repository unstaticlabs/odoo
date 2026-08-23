def migrate(cr, _version):
    obsolete_access_ids = (
        "access_platform_billing_platform_readonly",
        "access_platform_billing_platform_user",
        "access_platform_billing_session_readonly",
        "access_platform_billing_session_user",
        "access_platform_billing_payout_readonly",
        "access_platform_billing_payout_user",
        "access_platform_billing_bank_wizard_user",
        "access_platform_billing_bank_wizard_manager",
        "access_platform_billing_bank_wizard_line_user",
        "access_platform_billing_bank_wizard_line_manager",
    )
    cr.execute(
        """
        DELETE FROM ir_model_access
        WHERE id IN (
            SELECT res_id
              FROM ir_model_data
             WHERE module = 'usl_platform_billing'
               AND name = ANY(%s)
               AND model = 'ir.model.access'
        )
        """,
        [list(obsolete_access_ids)],
    )
    cr.execute(
        """
        DELETE FROM ir_model_data
         WHERE module = 'usl_platform_billing'
           AND name = ANY(%s)
           AND model = 'ir.model.access'
        """,
        [list(obsolete_access_ids)],
    )
    cr.execute(
        """
        ALTER TABLE usl_platform_billing_payout
        DROP CONSTRAINT IF EXISTS
            usl_platform_billing_payout_bank_statement_line_unique
        """,
    )
