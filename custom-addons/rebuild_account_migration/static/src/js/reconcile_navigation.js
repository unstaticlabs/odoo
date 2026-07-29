import { ReconcileFormNotebook } from "@account_reconcile_oca/views/reconcile_form/reconcile_form_notebook.esm";
import { ReconcileController } from "@account_reconcile_oca/views/reconcile_kanban/reconcile_controller.esm";
import { onMounted, onWillUnmount } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { router, routerBus } from "@web/core/browser/router";
import { patch } from "@web/core/utils/patch";

const BANK_MATCHING_HISTORY_MARKER = "rebuildBankMatching";
let activeBankMatchingController;
let pendingBankMatchingHistoryId;

export function getBankMatchingHistoryMode({
    initialLoad,
    requestedByUser,
    restoringHistory,
}) {
    if (restoringHistory) {
        return "restore";
    }
    if (initialLoad) {
        return "initial";
    }
    if (requestedByUser) {
        return "push";
    }
    return "replace";
}

export function getBankMatchingHistoryRecord({
    initialLoad,
    record,
    records,
    restoreInitialRoute = false,
    routedId,
    selectedRecordId,
}) {
    const normalizedRoutedId = Number(routedId);
    if (
        record !== undefined ||
        (initialLoad && !restoreInitialRoute) ||
        !normalizedRoutedId ||
        normalizedRoutedId === selectedRecordId
    ) {
        return null;
    }
    return records.find(({ resId }) => resId === normalizedRoutedId) || null;
}

export function isBankMatchingHistoryEntry(state) {
    return Boolean(state?.[BANK_MATCHING_HISTORY_MARKER]);
}

function setCurrentBankMatchingHistoryEntry(enabled) {
    const historyState = {...browser.history.state};
    if (enabled) {
        historyState[BANK_MATCHING_HISTORY_MARKER] = true;
        historyState.skipRouteChange = true;
    } else if (isBankMatchingHistoryEntry(historyState)) {
        delete historyState[BANK_MATCHING_HISTORY_MARKER];
        delete historyState.skipRouteChange;
    }
    browser.history.replaceState(
        historyState,
        "",
        browser.location.href,
    );
}

// Odoo reloads ordinary window actions on popstate. Transaction-to-transaction
// navigation is local to the mounted reconciliation controller, while returning
// from another action must still use Odoo's normal route loader.
browser.addEventListener("popstate", (event) => {
    if (!isBankMatchingHistoryEntry(event.state)) {
        return;
    }
    if (activeBankMatchingController) {
        activeBankMatchingController.restoreBankMatchingHistory();
    } else {
        pendingBankMatchingHistoryId = Number(event.state?.nextState?.id);
        routerBus.trigger("ROUTE_CHANGE");
    }
});

patch(ReconcileController.prototype, {
    setup() {
        super.setup(...arguments);
        onMounted(() => {
            activeBankMatchingController = this;
            this.rebuildBankMatchingMounted = true;
            this.scheduleInitialBankMatchingRoute();
        });
        onWillUnmount(() => {
            this.rebuildBankMatchingMounted = false;
            if (activeBankMatchingController === this) {
                activeBankMatchingController = undefined;
            }
            browser.clearTimeout(this.rebuildInitialRouteTimeout);
        });
    },
    scheduleInitialBankMatchingRoute() {
        if (
            !this.rebuildBankMatchingMounted ||
            !this.rebuildInitialHistoryId
        ) {
            return;
        }
        browser.clearTimeout(this.rebuildInitialRouteTimeout);
        this.rebuildInitialRouteTimeout = browser.setTimeout(() => {
            const resId = this.rebuildInitialHistoryId;
            if (
                !this.rebuildBankMatchingMounted ||
                !resId ||
                this.state.selectedRecordId !== resId
            ) {
                return;
            }
            this.rebuildInitialHistoryId = false;
            router.replaceState({ id: resId }, { sync: true });
        });
    },
    async restoreBankMatchingHistory() {
        this.rebuildRestoringHistory = true;
        try {
            await this.selectRecord();
        } finally {
            this.rebuildRestoringHistory = false;
            setCurrentBankMatchingHistoryEntry(false);
        }
    },
    async selectRecord(record) {
        const requestedByUserId = record?.resId;
        if (requestedByUserId) {
            this.rebuildHistorySelectionId = requestedByUserId;
        }
        const historyRecord = getBankMatchingHistoryRecord({
            initialLoad: this.initialLoad,
            record,
            records: this.model.root.records,
            restoreInitialRoute: Boolean(pendingBankMatchingHistoryId),
            routedId: pendingBankMatchingHistoryId || router.current?.id,
            selectedRecordId: this.state.selectedRecordId,
        });
        try {
            return await super.selectRecord(historyRecord || record);
        } finally {
            if (pendingBankMatchingHistoryId) {
                pendingBankMatchingHistoryId = undefined;
            }
            if (this.rebuildHistorySelectionId === requestedByUserId) {
                this.rebuildHistorySelectionId = false;
            }
        }
    },
    updateURL(resId) {
        const mode = getBankMatchingHistoryMode({
            initialLoad: this.initialLoad,
            requestedByUser: this.rebuildHistorySelectionId === resId,
            restoringHistory: this.rebuildRestoringHistory,
        });
        if (mode === "restore") {
            return;
        }
        if (mode === "initial") {
            this.rebuildInitialHistoryId = resId;
            this.scheduleInitialBankMatchingRoute();
            return;
        }
        if (mode === "push") {
            // Mark the line being left so Back can restore it locally. The new
            // top entry remains ordinary, allowing the next Back to cross the
            // Accounting-action boundary once the line trail is exhausted.
            setCurrentBankMatchingHistoryEntry(true);
            router.pushState({ id: resId }, { sync: true });
        } else {
            router.replaceState({ id: resId }, { sync: true });
        }
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
