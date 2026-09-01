{
    "name": "USL Pocket ID SSO",
    "summary": "Hardened Pocket ID OIDC login and governed identity linking",
    "version": "saas~19.3.1.1.1",
    "category": "Tools",
    "author": "Unstatic Labs",
    "license": "LGPL-3",
    "depends": [
        "account",
        "auth_oidc",
        "auth_passkey",
        "hr_expense",
        "mail",
        "portal",
        "project",
    ],
    "data": [
        "security/ir.model.access.csv",
        "data/pocketid_provider.xml",
        "data/mail_templates.xml",
        "views/auth_oauth_provider_views.xml",
        "views/oidc_identity_views.xml",
        "views/res_users_views.xml",
        "views/login_templates.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "usl_pocketid/static/src/js/check_identity.js",
            "usl_pocketid/static/src/xml/check_identity.xml",
        ],
    },
    "application": False,
    "installable": True,
}
