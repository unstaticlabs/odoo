"""Source chatter disposition policy shared by extraction and tests."""

EXPECTED_COUNTS = {
    "activities": 918,
    "aliases": 29,
    "attachment_relations": 558,
    "cross_accounting_parent_links": 1643,
    "followers": 6010,
    "mail_queue": 31,
    "messages": 51491,
    "notifications": 78,
    "parent_links": 24069,
    "tracking": 37579,
}

EXPECTED_MESSAGE_DISPOSITIONS = {
    "visible": 50588,
    "external": 0,
    "deliberately_not_copied": 903,
}

DIRECT_MODELS = frozenset({
    "account.account",
    "account.analytic.account",
    "account.asset",
    "account.bank.statement.line",
    "account.journal",
    "account.move",
    "account.payment",
    "account.reconcile.model",
    "account.tax",
    "hr.department",
    "hr.employee",
    "hr.expense",
    "hr.job",
    "hr.version",
    "product.category",
    "product.pricelist",
    "product.product",
    "product.template",
    "project.milestone",
    "project.project",
    "project.task",
    "project.update",
    "res.company",
    "res.partner",
    "res.partner.bank",
})

TRANSLATED_MODELS = {
    "account.return": "rebuild.account.declaration",
    "account.return.type": "rebuild.account.declaration.rule",
    "documents.document": "usl.document",
    "sign.request": "usl.document",
}

DELIBERATELY_NOT_COPIED_MODELS = frozenset({
    "knowledge.article",
})

EXTERNAL_ARCHIVE_MODELS = frozenset({
    "account.depreciation.model",
    "account.online.link",
    "base.automation",
    "crm.team",
    "crm.team.member",
    "iap.account",
    "ir.actions.server",
    "ir.cron",
    "quality.alert.team",
})

DISCARD_MODELS = frozenset({
    "mail.notification",
    "mail.presence",
    "mail.push.device",
})


def route_model(model):
    if model in DIRECT_MODELS:
        return "native"
    if model in TRANSLATED_MODELS:
        return "translated"
    if model == "discuss.channel":
        return "discuss"
    if model in DELIBERATELY_NOT_COPIED_MODELS:
        return "deliberately_not_copied"
    if model in EXTERNAL_ARCHIVE_MODELS or not model:
        return "external_archive"
    return "unclassified"


def route_technical_table(table):
    recompute_prefixes = (
        "mail_activity_plan", "mail_canned_response", "mail_message_subtype",
        "mail_template", "sms_template", "mail_alias_domain",
    )
    discard_prefixes = (
        "discuss_call_history", "discuss_channel_rtc", "mail_compose_message",
        "mail_followers_edit", "mail_ice_server", "mail_presence", "mail_push",
        "mail_scheduled_message", "mail_template_preview", "mail_template_reset",
    )
    if table.startswith(recompute_prefixes):
        return "xmlid_or_installed_module_recompute"
    if table.startswith(discard_prefixes):
        return "discard_transient_state"
    return "private_archive"
