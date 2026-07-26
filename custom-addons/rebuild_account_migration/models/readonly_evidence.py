from odoo import api, models
from odoo.exceptions import AccessError


def _is_accounting_evidence_model(model_name):
    return (
        model_name == "hr.expense"
        or model_name.startswith(("account.", "rebuild.account."))
    )


def _is_persistent_accounting_evidence_model(env, model_name):
    if not _is_accounting_evidence_model(model_name):
        return False
    return model_name not in env or not env[model_name].is_transient()


def _is_scoped_reviewer(env):
    user = env.user
    return (
        user.has_group(
            "rebuild_account_migration.group_rebuild_accountant_reviewer",
        )
        and not user.has_group("account.group_account_user")
    )


class MailThread(models.AbstractModel):
    _inherit = "mail.thread"

    def message_post(self, *args, **kwargs):
        if (
            _is_scoped_reviewer(self.env)
            and _is_accounting_evidence_model(self._name)
        ):
            raise AccessError(
                self.env._(
                    "The accountant review role can inspect accounting evidence "
                    "but cannot add messages or attachments.",
                ),
            )
        return super().message_post(*args, **kwargs)


class IrAttachment(models.Model):
    _inherit = "ir.attachment"

    @api.model_create_multi
    def create(self, vals_list):
        if _is_scoped_reviewer(self.env) and any(
            _is_persistent_accounting_evidence_model(
                self.env,
                vals.get("res_model", ""),
            )
            for vals in vals_list
        ):
            raise AccessError(
                self.env._(
                    "The accountant review role cannot add accounting attachments.",
                ),
            )
        return super().create(vals_list)

    def write(self, vals):
        if _is_scoped_reviewer(self.env) and any(
            _is_persistent_accounting_evidence_model(
                self.env,
                attachment.res_model or "",
            )
            for attachment in self
        ):
            raise AccessError(
                self.env._(
                    "The accountant review role cannot change accounting attachments.",
                ),
            )
        return super().write(vals)

    def unlink(self):
        if _is_scoped_reviewer(self.env) and any(
            _is_persistent_accounting_evidence_model(
                self.env,
                attachment.res_model or "",
            )
            for attachment in self
        ):
            raise AccessError(
                self.env._(
                    "The accountant review role cannot remove accounting attachments.",
                ),
            )
        return super().unlink()
