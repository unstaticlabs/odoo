def migrate(cr, version):
    """Existing Agents keep their delegated read/write behavior on upgrade."""

    if not version:
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
