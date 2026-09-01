import { HrUserPreferencesController } from "@hr/views/preferences_form_view";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { formView } from "@web/views/form/form_view";

export class UslUserPreferencesController extends HrUserPreferencesController {
    setup() {
        super.setup();
        this.notification = useService("notification");
        this.keepOpenForSenderAliases = false;
    }

    onWillSaveRecord(record, changes) {
        const result = super.onWillSaveRecord(record, changes);
        this.keepOpenForSenderAliases ||= "usl_sender_alias_ids" in changes;
        return result;
    }

    async beforeExecuteActionButton(clickParams) {
        if (
            clickParams.name === "preference_save" ||
            clickParams.special === "save" ||
            clickParams.special === "cancel"
        ) {
            return super.beforeExecuteActionButton(clickParams);
        }
        const record = this.model.root;
        return record.save({ reload: !(this.env.inDialog && clickParams.close) });
    }

    async save(params = {}) {
        const record = this.model.root;
        this.keepOpenForSenderAliases = false;
        let saved;
        if (this.props.saveRecord) {
            saved = await this.props.saveRecord(record, params);
        } else {
            saved = await record.save({
                onError: (error, options) => this.onSaveError(error, options, false),
                ...params,
            });
        }
        if (saved && this.keepOpenForSenderAliases && this.env.inDialog) {
            this.notification.add(
                _t(
                    "We sent a verification link to each new or changed address. Check your inbox within 24 hours."
                ),
                {
                    title: _t("Verification email sent"),
                    type: "success",
                }
            );
        } else if (saved && this.props.onSave) {
            this.props.onSave(record, params);
        }
        return saved;
    }
}

registry.category("views").add("usl_user_preferences_form", {
    ...formView,
    Controller: UslUserPreferencesController,
});
