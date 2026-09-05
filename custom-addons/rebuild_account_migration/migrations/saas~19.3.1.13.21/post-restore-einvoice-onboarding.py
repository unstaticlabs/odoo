from odoo import SUPERUSER_ID, api, fields


def _field_id(env, name):
    return env["ir.model.fields"]._get("res.company", name).id


def migrate(cr, version):
    """Repair onboarding state erased by the 13.20 offline upgrade hook.

    The faulty hook wrote the environment and approval fields together as the
    system user. A legitimate deactivation is performed by a human-facing
    product action, so requiring both changes in the same system-authored audit
    message makes this repair fail closed.
    """
    del version
    env = api.Environment(cr, SUPERUSER_ID, {})
    company_model = env["res.company"].sudo().with_context(
        tracking_disable=False,
    )
    system_partner = env.ref("base.partner_root")
    environment_field_id = _field_id(env, "rebuild_einvoice_environment")
    approval_field_id = _field_id(
        env,
        "rebuild_einvoice_activation_approved",
    )

    cr.execute(
        """
        SELECT DISTINCT message.res_id, message.id, message.date
          FROM mail_message AS message
          JOIN mail_tracking_value AS environment_tracking
            ON environment_tracking.mail_message_id = message.id
           AND environment_tracking.field_id = %s
           AND environment_tracking.old_value_char = 'Production'
           AND environment_tracking.new_value_char = 'Development or Test'
          JOIN mail_tracking_value AS approval_tracking
            ON approval_tracking.mail_message_id = message.id
           AND approval_tracking.field_id = %s
           AND approval_tracking.old_value_integer = 1
           AND approval_tracking.new_value_integer = 0
         WHERE message.model = 'res.company'
           AND message.author_id = %s
         ORDER BY message.id
        """,
        (environment_field_id, approval_field_id, system_partner.id),
    )

    for company_id, reset_message_id, reset_at in cr.fetchall():
        company = company_model.browse(company_id).exists()
        if not company or (
            company.rebuild_einvoice_environment != "development"
            or company.rebuild_einvoice_activation_approved
        ):
            continue
        if (
            company.account_peppol_proxy_state != "smp_registration"
            or company.pdp_kyc_status != "success"
        ):
            continue

        cr.execute(
            """
            SELECT message.date, users.id
              FROM mail_tracking_value AS tracking
              JOIN mail_message AS message
                ON message.id = tracking.mail_message_id
              JOIN res_users AS users
                ON users.partner_id = message.author_id
             WHERE message.model = 'res.company'
               AND message.res_id = %s
               AND message.id < %s
               AND message.date <= %s
               AND tracking.field_id = %s
               AND tracking.old_value_integer = 0
               AND tracking.new_value_integer = 1
             ORDER BY message.id DESC
             LIMIT 1
            """,
            (
                company.id,
                reset_message_id,
                reset_at,
                approval_field_id,
            ),
        )
        approval = cr.fetchone()
        if not approval:
            continue
        approved_at, approved_by_id = approval
        company.write({
            "rebuild_einvoice_environment": "production",
            "rebuild_einvoice_production_prepared_by_id": approved_by_id,
            "rebuild_einvoice_production_prepared_at": fields.Datetime.to_datetime(
                approved_at,
            ),
            "rebuild_einvoice_activation_approved": True,
            "rebuild_einvoice_approved_by_id": approved_by_id,
            "rebuild_einvoice_approved_at": fields.Datetime.to_datetime(approved_at),
        })
