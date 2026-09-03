{
    "name": "USL Bootstrap",
    "version": "saas~19.3.1.0.1",
    "category": "Customization",
    "summary": "Reproducible Unstatic Labs local demo baseline.",
    "author": "Unstatic Labs",
    "license": "LGPL-3",
    "depends": [
        "contacts",
        "hr_expense",
        "l10n_fr_account",
        "project",
        "sale_management",
    ],
    "data": [
        "data/bootstrap_meta.xml",
    ],
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": False,
}
