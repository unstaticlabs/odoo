CANONICAL_PREPARATION_MESSAGE = (
    "Preparation complete: the payroll entry is balanced and posted, and "
    "the official TESE PDF is linked."
)


def migrate(cr, version):
    del version
    cr.execute(
        """
        UPDATE usl_tese_payslip payslip
           SET preparation_ok = TRUE,
               preparation_message = %s
          FROM account_move payroll_move
         WHERE payroll_move.id = payslip.move_id
           AND payroll_move.state = 'posted'
           AND payslip.attachment_id IS NOT NULL
           AND payslip.state IN ('to_reconcile', 'paid')
           AND (
               payslip.preparation_ok IS NOT TRUE
               OR payslip.preparation_message IS DISTINCT FROM %s
           )
        """,
        (CANONICAL_PREPARATION_MESSAGE, CANONICAL_PREPARATION_MESSAGE),
    )
