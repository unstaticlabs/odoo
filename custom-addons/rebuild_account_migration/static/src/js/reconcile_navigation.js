import { ReconcileFormNotebook } from "@account_reconcile_oca/views/reconcile_form/reconcile_form_notebook.esm";
import { ReconcileController } from "@account_reconcile_oca/views/reconcile_kanban/reconcile_controller.esm";
import { router } from "@web/core/browser/router";
import { patch } from "@web/core/utils/patch";

patch(ReconcileController.prototype, {
    updateURL(resId) {
        router.replaceState({ id: resId });
    },
});

patch(ReconcileFormNotebook.prototype, {
    setup() {
        super.setup(...arguments);
        const defaultPageName = this.env.model.root.data.is_reconciled
            ? "chatter"
            : "reconcile_line";
        const defaultPage = this.pages.find(
            ([, page]) => page.name === defaultPageName,
        );
        if (defaultPage) {
            this.state.currentPage = defaultPage[0];
        }
    },
});
