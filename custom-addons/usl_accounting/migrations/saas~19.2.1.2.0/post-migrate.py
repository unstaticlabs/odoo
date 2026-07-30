from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})

    legacy_settlements = env["account.immediate.settlement"].search(
        [("adjustment_move_id", "!=", False)],
    )
    for settlement in legacy_settlements:
        settlement.write(
            {
                "mechanism": "legacy_adjustment",
                "source_line_id_snapshot": settlement.payment_line_id.id,
            },
        )

    cr.execute(
        """
        SELECT EXISTS (
            SELECT 1
              FROM information_schema.columns
             WHERE table_name = 'res_company'
               AND column_name = 'immediate_settlement_journal_id'
        )
        """,
    )
    if not cr.fetchone()[0]:
        return

    cr.execute(
        """
        SELECT immediate_settlement_journal_id
          FROM res_company
         WHERE immediate_settlement_journal_id IS NOT NULL
        """,
    )
    generated_journal_ids = [journal_id for [journal_id] in cr.fetchall()]
    generated_journals = env["account.journal"].search(
        [
            ("id", "in", generated_journal_ids),
            ("active", "=", True),
        ],
    )
    for journal in generated_journals:
        if not env["account.move"].search_count(
            [("journal_id", "=", journal.id)],
            limit=1,
        ):
            journal.active = False
