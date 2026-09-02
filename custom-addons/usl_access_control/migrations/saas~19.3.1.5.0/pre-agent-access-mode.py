def migrate(cr, version):
    """Existing Agents keep their delegated read/write behavior on upgrade."""

    if not version:
        return
    # A direct upgrade from the pre-Agent product (1.3.x) reaches this
    # migration before Odoo creates the new ``usl_agent`` model table.
    # Upgrades from the earlier Agent implementation already have the table.
    cr.execute("SELECT to_regclass('usl_agent')")
    if not cr.fetchone()[0]:
        return
    cr.execute(
        """
        ALTER TABLE usl_agent
        ADD COLUMN IF NOT EXISTS access_mode varchar
        """,
    )
    cr.execute(
        """
        UPDATE usl_agent
           SET access_mode = 'read_write'
         WHERE access_mode IS NULL
        """,
    )
