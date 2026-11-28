import { useChildSubEnv } from "@web/owl2/utils";
import { registry } from "@web/core/registry";
import { deepEqual } from "@web/core/utils/objects";
import { formView } from "@web/views/form/form_view";
import { SearchModel } from "@web/search/search_model";
import {
    buildM2OFieldDescription,
    Many2OneField,
} from "@web/views/fields/many2one/many2one_field";
import { View } from "@web/views/view";
import { SelectCreateDialog } from "@web/views/view_dialogs/select_create_dialog";

class TransferSearchModel extends SearchModel {
    get context() {
        // The regular list view resets the field's companies to the user's active companies.
        return { ...super.context, allowed_company_ids: this.globalContext.allowed_company_ids };
    }
}

class TransferLookupView extends View {
    async loadView(props) {
        await super.loadView(props);
        this.withSearchProps.SearchModel = TransferSearchModel;
    }
}

class TransferLookupDialog extends SelectCreateDialog {
    static components = { ...SelectCreateDialog.components, View: TransferLookupView };
}

// Both the mobile selector and desktop "Search more…" open the native selection dialog.
// Keep its list in the exact same company scope as the inline field.
export function useTransferLookup(services) {
    const dialog = services.dialog;
    useChildSubEnv({
        services: {
            ...services,
            dialog: {
                ...dialog,
                add(component, props, options) {
                    if (component === SelectCreateDialog && props.context?.allowed_company_ids) {
                        component = TransferLookupDialog;
                    }
                    return dialog.add(component, props, options);
                },
            },
        },
    });
}

class TransferCompanyField extends Many2OneField {
    setup() {
        super.setup();
        useTransferLookup(this.env.services);
    }
}

registry.category("fields").add("usl_bill_transfer_company", buildM2OFieldDescription(TransferCompanyField));

class TransferWizardModel extends formView.Model {
    async load(params = {}) {
        const mapping = this.root?.data.mapping_ids;
        // Re-rendering the wizard with unchanged search props must keep the record
        // that its mobile line editor is still editing.
        if (params.context && deepEqual(params, this.lastSearch) &&
            mapping?.records.some((line) => line.isInEdition)) {
            return;
        }
        const result = await super.load(params);
        this.lastSearch = params;
        return result;
    }
}

registry.category("views").add("usl_bill_company_transfer", { ...formView, Model: TransferWizardModel });
