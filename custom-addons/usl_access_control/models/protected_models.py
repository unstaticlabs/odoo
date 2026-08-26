from odoo import api, models


class ResCompany(models.Model):
    _inherit = "res.company"

    @api.model_create_multi
    def create(self, vals_list):
        self._usl_require_irreversible_action("create a company security boundary")
        return super().create(vals_list)

    def write(self, values):
        protected = {
            "fiscalyear_lock_date",
            "hard_lock_date",
            "purchase_lock_date",
            "sale_lock_date",
            "tax_lock_date",
        }
        if protected & set(values):
            self._usl_require_irreversible_action("change accounting lock dates")
        return super().write(values)


class IrActionsServer(models.Model):
    _inherit = "ir.actions.server"

    def run(self):
        self._usl_require_irreversible_action("execute a server action")
        return super().run()


class IrCron(models.Model):
    _inherit = "ir.cron"

    def method_direct_trigger(self):
        self._usl_require_irreversible_action("run a scheduled action manually")
        return super().method_direct_trigger()


class IrModuleModule(models.Model):
    _inherit = "ir.module.module"

    def button_immediate_install(self):
        self._usl_require_irreversible_action("install a module")
        return super().button_immediate_install()

    def button_immediate_upgrade(self):
        self._usl_require_irreversible_action("upgrade a module")
        return super().button_immediate_upgrade()

    def button_immediate_uninstall(self):
        self._usl_require_irreversible_action("uninstall a module")
        return super().button_immediate_uninstall()


class B2cAccountingSession(models.Model):
    _inherit = "b2c.accounting.session"

    def action_unlock(self):
        self._usl_require_irreversible_action("unlock a governed B2C accounting session")
        return super().action_unlock()


class UslDocument(models.Model):
    _inherit = "usl.document"

    def document_detail(self, document_id, check_archive=False):
        result = super().document_detail(document_id, check_archive=check_archive)
        result["can_permanently_delete"] = self._usl_actor_may_perform_irreversible_actions()
        return result

    def approve_permanent_deletion(self, reason):
        self._usl_require_irreversible_action("approve permanent document deletion")
        return super().approve_permanent_deletion(reason)

    def permanently_delete_from_trash(self):
        self._usl_require_irreversible_action("permanently delete a document")
        return super().permanently_delete_from_trash()


class PdpRegistration(models.TransientModel):
    _inherit = "pdp.registration"

    def button_register_pdp_participant(self):
        self._usl_require_irreversible_action("register with an electronic-invoice platform")
        return super().button_register_pdp_participant()

    def button_deregister_pdp_participant(self):
        self._usl_require_irreversible_action("deregister from an electronic-invoice platform")
        return super().button_deregister_pdp_participant()


class PeppolRegistration(models.TransientModel):
    _inherit = "peppol.registration"

    def button_register_peppol_participant(self, selected_auth=None):
        self._usl_require_irreversible_action("register with Peppol")
        return super().button_register_peppol_participant(selected_auth=selected_auth)


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    def button_peppol_disconnect_branch_from_parent(self):
        self._usl_require_irreversible_action("disconnect a branch from Peppol")
        return super().button_peppol_disconnect_branch_from_parent()

    def button_reconnect_this_database(self):
        self._usl_require_irreversible_action("reconnect this database to Peppol")
        return super().button_reconnect_this_database()

    def button_disconnect_this_database(self):
        self._usl_require_irreversible_action("disconnect this database from Peppol")
        return super().button_disconnect_this_database()

    def button_peppol_deregister(self):
        self._usl_require_irreversible_action("deregister from Peppol")
        return super().button_peppol_deregister()

    def button_peppol_reregister(self):
        self._usl_require_irreversible_action("reregister with Peppol")
        return super().button_peppol_reregister()


class PdpConfigWizard(models.TransientModel):
    _inherit = "pdp.config.wizard"

    def button_sync_form_with_peppol_proxy(self):
        self._usl_require_irreversible_action("change Peppol provider configuration")
        return super().button_sync_form_with_peppol_proxy()

    def button_peppol_unregister(self):
        self._usl_require_irreversible_action("unregister from Peppol")
        return super().button_peppol_unregister()
