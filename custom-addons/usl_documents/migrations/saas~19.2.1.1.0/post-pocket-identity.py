def migrate(cr, version):
    del version
    # A username match is no longer sufficient for interactive archive access.
    # Existing mappings must be explicitly verified against the user's durable
    # Pocket identity. Isolated QA re-seeds its documented local-login exception
    # immediately after the module update.
    cr.execute(
        """
        UPDATE usl_paperless_user_mapping
           SET sync_state = 'pending',
               last_verified_at = NULL,
               last_error = (
                   'Verify this Paperless account against the user''s '
                   'Pocket ID identity.'
               )
         WHERE active
           AND sync_state = 'synchronized'
           AND oidc_identity_id IS NULL
           AND NOT qa_local_identity
        """,
    )
