import {
    AccountReconcileDataWidget,
    AccountReconcileDataWidgetField,
} from "@account_reconcile_oca/components/account_reconcile_oca_data/account_reconcile_oca_data.esm";
import {registry} from "@web/core/registry";

/**
 * Read-only presentation of OCA's reconciliation data.
 *
 * The component deliberately inherits OCA's formatter and data contract. Only
 * the template changes: transaction history must expose the same accounting
 * lines without selecting or deleting a proposed counterpart.
 */
export class ReconcileDataPresentation extends AccountReconcileDataWidget {
    static template = "rebuild_account_migration.ReconcileDataPresentation";
}

export const reconcileDataPresentationField = {
    ...AccountReconcileDataWidgetField,
    component: ReconcileDataPresentation,
};

registry
    .category("fields")
    .add("rebuild_reconcile_data_presentation", reconcileDataPresentationField);
