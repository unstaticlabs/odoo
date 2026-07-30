def migrate(cr, version):
    del version
    cr.execute(
        """
        SELECT 1
          FROM information_schema.columns
         WHERE table_name = 'usl_document_quick_filter'
           AND column_name = 'filter_type'
        """
    )
    if not cr.fetchone():
        return
    # A server can load the new registry before the explicit module upgrade.
    # Fill any resulting nullable columns before Odoo installs the required
    # constraints.
    cr.execute(
        """
        UPDATE usl_document_quick_filter
           SET filter_type = COALESCE(filter_type, 'unlinked'),
               days = COALESCE(days, 30),
               confidentiality = COALESCE(confidentiality, 'internal')
         WHERE filter_type IS NULL
            OR days IS NULL
            OR confidentiality IS NULL
        """
    )
