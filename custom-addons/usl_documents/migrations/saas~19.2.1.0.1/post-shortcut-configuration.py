def migrate(cr, version):
    del version
    # ``usl_documents_data.xml`` is deliberately noupdate so managers retain
    # their placement and labels. Set only the newly introduced typed values
    # for the stable built-in shortcuts on upgraded databases.
    cr.execute(
        """
        UPDATE usl_document_quick_filter
           SET filter_type = CASE key
               WHEN 'my_uploads' THEN 'my_uploads'
               WHEN 'unlinked' THEN 'unlinked'
               WHEN 'needs_review' THEN 'needs_review'
               WHEN 'last_30_days' THEN 'recent'
               ELSE filter_type
           END,
               days = CASE
                   WHEN key = 'last_30_days' THEN 30
                   ELSE days
               END
         WHERE key IN (
             'my_uploads',
             'unlinked',
             'needs_review',
             'last_30_days'
         )
        """
    )
