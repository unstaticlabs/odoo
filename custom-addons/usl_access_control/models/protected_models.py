from odoo import api, models


class ResCompany(models.Model):
    _inherit = "res.company"

    @api.model_create_multi
    def create(self, vals_list):
        self._usl_require_irreversible_action(
            "authorization.company.create",
            "create a company security boundary",
        )
        return super().create(vals_list)

    def write(self, values):
        protected = {
            "fiscalyear_lock_date",
            "hard_lock_date",
            "purchase_lock_date",
            "sale_lock_date",
            "tax_lock_date",
        }
        if protected & set(values) and not self.env.context.get(
            "usl_accounting_lock_guarded",
        ):
            self._usl_require_irreversible_action(
                "accounting.lock.change",
                "change accounting lock dates",
            )
        return super().write(values)

    def install_l10n_modules(self):
        self._usl_require_irreversible_action(
            "module.install",
            "install company localization modules",
        )
        return super().install_l10n_modules()


class AccountFiscalPosition(models.Model):
    _inherit = "account.fiscal.position"

    def action_create_foreign_taxes(self):
        self._usl_require_irreversible_action(
            "module.install",
            "install a foreign localization and create its taxes",
        )
        return super().action_create_foreign_taxes()


class IrActionsServer(models.Model):
    _inherit = "ir.actions.server"

    def _usl_check_qualified_execution(self):
        policy = self._usl_qualified_action_policy()
        external_ids = self.sudo().get_external_id()
        for action in self:
            xmlid = external_ids.get(action.id)
            action_key = f"server_action:{xmlid}" if xmlid else False
            classification = (
                policy.server_action_classification(action_key)
                if action_key
                else None
            )
            if classification == "protected":
                action._usl_require_irreversible_action(
                    action_key,
                    f"execute protected server action {action.name}",
                    exact_policy_key=True,
                )
            elif classification is None:
                action._usl_require_irreversible_action(
                    "automation.server_action.execute",
                    f"execute unreviewed server action {action.name}",
                )

    def run(self):
        self._usl_check_qualified_execution()
        return super().run()


class IrAttachment(models.Model):
    _inherit = "ir.attachment"

    def _usl_governed_attachments(self):
        governed = self.browse()
        for attachment in self:
            if not attachment.res_model or not attachment.res_id:
                continue
            model = self.env.registry.get(attachment.res_model)
            if model is not None and model._transient:
                continue
            governed |= attachment
        return governed

    def unlink(self):
        governed = self._usl_governed_attachments()
        if governed:
            governed._usl_require_irreversible_action(
                "evidence.attachment.delete",
                "permanently delete attachment evidence from a business record",
            )
        return super().unlink()


class IrCron(models.Model):
    _inherit = "ir.cron"

    def method_direct_trigger(self):
        self.ir_actions_server_id._usl_check_qualified_execution()
        return super().method_direct_trigger()


class IrModuleModule(models.Model):
    _inherit = "ir.module.module"

    def _import_zipfile(self, module_file, force=False, with_demo=False):
        self._usl_require_irreversible_action(
            "module.install",
            "import application module code",
        )
        return super()._import_zipfile(
            module_file,
            force=force,
            with_demo=with_demo,
        )

    def button_install(self):
        self._usl_require_irreversible_action("module.install", "install a module")
        return super().button_install()

    def button_immediate_install(self):
        self._usl_require_irreversible_action("module.install", "install a module")
        return super().button_immediate_install()

    def button_immediate_install_app(self):
        self._usl_require_irreversible_action(
            "module.install",
            "download an application module",
        )
        return super().button_immediate_install_app()

    def button_upgrade(self):
        self._usl_require_irreversible_action("module.upgrade", "upgrade a module")
        return super().button_upgrade()

    def button_immediate_upgrade(self):
        self._usl_require_irreversible_action("module.upgrade", "upgrade a module")
        return super().button_immediate_upgrade()

    def button_uninstall(self):
        self._usl_require_irreversible_action("module.uninstall", "uninstall a module")
        return super().button_uninstall()

    def button_immediate_uninstall(self):
        self._usl_require_irreversible_action("module.uninstall", "uninstall a module")
        return super().button_immediate_uninstall()

    def module_uninstall(self):
        self._usl_require_irreversible_action("module.uninstall", "uninstall a module")
        return super().module_uninstall()


class BaseModuleInstallReview(models.TransientModel):
    _inherit = "base.module.install.review"

    def action_install_module(self):
        self._usl_require_irreversible_action("module.install", "install a module")
        return super().action_install_module()


class BaseModuleUninstall(models.TransientModel):
    _inherit = "base.module.uninstall"

    def action_uninstall(self):
        self._usl_require_irreversible_action("module.uninstall", "uninstall a module")
        return super().action_uninstall()


class PaymentProvider(models.Model):
    _inherit = "payment.provider"

    def button_immediate_install(self):
        self._usl_require_irreversible_action(
            "module.install",
            "install a payment provider module",
        )
        return super().button_immediate_install()


class B2cAccountingSession(models.Model):
    _inherit = "b2c.accounting.session"

    def action_unlock(self):
        self._usl_require_irreversible_action(
            "accounting.b2c_session.unlock",
            "unlock a governed B2C accounting session",
        )
        return super().action_unlock()


class RebuildAccountClosingPeriod(models.Model):
    _inherit = "rebuild.account.closing.period"

    def action_close_and_apply_lock_dates(self):
        self._usl_require_irreversible_action(
            "accounting.lock.change",
            "close the accounting period and change lock dates",
        )
        return super(
            RebuildAccountClosingPeriod,
            self.with_context(usl_accounting_lock_guarded=True),
        ).action_close_and_apply_lock_dates()


class UslDocument(models.Model):
    _inherit = "usl.document"

    @api.model
    def document_detail(self, document_id, check_archive=False):
        result = super().document_detail(document_id, check_archive=check_archive)
        result["can_permanently_delete"] = self._usl_actor_may_perform_irreversible_actions()
        return result

    def approve_permanent_deletion(self, reason):
        if not self.env.context.get("usl_document_deletion_approval_guarded"):
            self._usl_require_irreversible_action(
                "documents.permanent_deletion.approve",
                "approve permanent document deletion",
            )
        return super().approve_permanent_deletion(reason)

    def action_approve_permanent_deletion(self):
        self._usl_require_irreversible_action(
            "documents.permanent_deletion.approve",
            "approve permanent document deletion",
        )
        return super(
            UslDocument,
            self.with_context(usl_document_deletion_approval_guarded=True),
        ).action_approve_permanent_deletion()

    def permanently_delete_from_trash(self):
        if not self.env.context.get("usl_document_permanent_delete_guarded"):
            self._usl_require_irreversible_action(
                "documents.permanent_delete",
                "permanently delete a document",
            )
        return super().permanently_delete_from_trash()

    def action_permanently_delete_from_trash(self):
        self._usl_require_irreversible_action(
            "documents.permanent_delete",
            "permanently delete a document",
        )
        return super(
            UslDocument,
            self.with_context(usl_document_permanent_delete_guarded=True),
        ).action_permanently_delete_from_trash()


class UslDocumentLetter(models.Model):
    _inherit = "usl.document.letter"

    def action_finalize(self):
        self._usl_require_irreversible_action(
            "documents.letter.finalize",
            "issue an immutable official letter",
        )
        return super().action_finalize()

    def action_mark_sent(self):
        self._usl_require_irreversible_action(
            "documents.letter.mark_sent",
            "record an official letter as sent",
        )
        return super().action_mark_sent()

    def action_cancel(self):
        if any(letter.state == "finalized" for letter in self):
            self._usl_require_irreversible_action(
                "documents.letter.cancel_issued",
                "cancel an issued official letter",
            )
        return super().action_cancel()


class PdpRegistration(models.TransientModel):
    _inherit = "pdp.registration"

    def button_register_pdp_participant(self):
        self._usl_require_irreversible_action(
            "einvoice.pdp.register",
            "register with an electronic-invoice platform",
        )
        return super().button_register_pdp_participant()

    def button_deregister_pdp_participant(self):
        self._usl_require_irreversible_action(
            "einvoice.pdp.deregister",
            "deregister from an electronic-invoice platform",
        )
        return super().button_deregister_pdp_participant()


class PeppolRegistration(models.TransientModel):
    _inherit = "peppol.registration"

    def button_register_peppol_participant(self, selected_auth=None):
        self._usl_require_irreversible_action(
            "einvoice.peppol.register",
            "register with Peppol",
        )
        return super().button_register_peppol_participant(selected_auth=selected_auth)


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    def action_eu_oss_tax_mapping(self):
        self._usl_require_irreversible_action(
            "module.install",
            "install EU OSS tax mapping",
        )
        return super().action_eu_oss_tax_mapping()

    def button_peppol_disconnect_branch_from_parent(self):
        self._usl_require_irreversible_action(
            "einvoice.peppol.branch.disconnect",
            "disconnect a branch from Peppol",
        )
        return super().button_peppol_disconnect_branch_from_parent()

    def button_reconnect_this_database(self):
        self._usl_require_irreversible_action(
            "einvoice.peppol.database.reconnect",
            "reconnect this database to Peppol",
        )
        return super().button_reconnect_this_database()

    def button_disconnect_this_database(self):
        self._usl_require_irreversible_action(
            "einvoice.peppol.database.disconnect",
            "disconnect this database from Peppol",
        )
        return super().button_disconnect_this_database()

    def button_peppol_deregister(self):
        self._usl_require_irreversible_action(
            "einvoice.peppol.deregister",
            "deregister from Peppol",
        )
        return super().button_peppol_deregister()

    def button_peppol_reregister(self):
        self._usl_require_irreversible_action(
            "einvoice.peppol.reregister",
            "reregister with Peppol",
        )
        return super().button_peppol_reregister()


class PdpConfigWizard(models.TransientModel):
    _inherit = "pdp.config.wizard"

    def button_sync_form_with_peppol_proxy(self):
        self._usl_require_irreversible_action(
            "einvoice.peppol.configuration.change",
            "change Peppol provider configuration",
        )
        return super().button_sync_form_with_peppol_proxy()

    def button_peppol_unregister(self):
        self._usl_require_irreversible_action(
            "einvoice.peppol.unregister",
            "unregister from Peppol",
        )
        return super().button_peppol_unregister()
