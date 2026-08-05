{
    "name": "USL Sign",
    "summary": "Native, evidence-preserving electronic signatures",
    "description": "Odoo-owned signature workflows with provider-backed trust services.",
    "version": "saas~19.2.1.0.0",
    "category": "Productivity/Sign",
    "author": "Unstatic Labs",
    "license": "LGPL-3",
    "depends": [
        "sign_oca",
        "mail",
        "portal",
        "usl_locale",
    ],
    "data": [
        "security/usl_sign_security.xml",
        "security/ir.model.access.csv",
        "data/sign_data.xml",
        "views/sign_policy_views.xml",
        "views/sign_template_views.xml",
        "views/sign_request_views.xml",
        "views/sign_menu_views.xml",
    ],
    "post_init_hook": "post_init_hook",
    "application": True,
    "installable": True,
}

