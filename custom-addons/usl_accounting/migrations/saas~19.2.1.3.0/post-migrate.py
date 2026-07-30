def migrate(cr, version):
    cr.execute(
        """
        UPDATE account_immediate_settlement
           SET preview_settlement_difference = settlement_difference,
               original_statement_foreign_amount_source = COALESCE(
                   original_statement_foreign_amount_source,
                   'missing'
               )
         WHERE mechanism = 'bank_statement'
        """,
    )
    cr.execute(
        """
        UPDATE account_immediate_settlement_allocation AS allocation
           SET adjustment_line_id_snapshot = allocation.adjustment_line_id,
               adjustment_line_name = (
                   SELECT line.name
                     FROM account_move_line AS line
                    WHERE line.id = allocation.adjustment_line_id
               ),
               account_id_snapshot = (
                   SELECT original.account_id
                     FROM account_move_line AS original
                    WHERE original.id = allocation.original_line_id
               )
         WHERE (
               allocation.adjustment_line_id_snapshot IS NULL
               OR allocation.account_id_snapshot IS NULL
           )
        """,
    )
