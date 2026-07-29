import {expect, test} from "@odoo/hoot";
import {patchWithCleanup} from "@web/../tests/web_test_helpers";

import {browser} from "@web/core/browser/browser";
import {router} from "@web/core/browser/router";
import {ReconcileController} from "@account_reconcile_oca/views/reconcile_kanban/reconcile_controller.esm";
import {
    getBankMatchingHistoryMode,
    getBankMatchingHistoryRecord,
    isBankMatchingHistoryEntry,
} from "../src/js/reconcile_navigation";

test("Bank Matching assigns history modes by selection source", () => {
    expect(
        getBankMatchingHistoryMode({
            initialLoad: true,
            requestedByUser: false,
            restoringHistory: false,
        }),
    ).toBe("initial");
    expect(
        getBankMatchingHistoryMode({
            initialLoad: false,
            requestedByUser: true,
            restoringHistory: false,
        }),
    ).toBe("push");
    expect(
        getBankMatchingHistoryMode({
            initialLoad: false,
            requestedByUser: false,
            restoringHistory: false,
        }),
    ).toBe("replace");
    expect(
        getBankMatchingHistoryMode({
            initialLoad: false,
            requestedByUser: false,
            restoringHistory: true,
        }),
    ).toBe("restore");
});

test("Bank Matching finds the line restored by browser history", () => {
    const first = {resId: 3046};
    const second = {resId: 3045};
    const records = [first, second];

    expect(
        getBankMatchingHistoryRecord({
            initialLoad: false,
            record: undefined,
            records,
            routedId: 3046,
            selectedRecordId: 3045,
        }),
    ).toBe(first);
    expect(
        getBankMatchingHistoryRecord({
            initialLoad: false,
            record: second,
            records,
            routedId: 3046,
            selectedRecordId: 3045,
        }),
    ).toBe(null);
    expect(
        getBankMatchingHistoryRecord({
            initialLoad: false,
            record: undefined,
            records,
            routedId: 9999,
            selectedRecordId: 3045,
        }),
    ).toBe(null);
    expect(
        getBankMatchingHistoryRecord({
            initialLoad: true,
            record: undefined,
            records,
            restoreInitialRoute: true,
            routedId: 3046,
            selectedRecordId: false,
        }),
    ).toBe(first);
});

test("automatic first selection waits for the Bank Matching action to mount", () => {
    ReconcileController.prototype.updateURL.call(
        {
            initialLoad: true,
            scheduleInitialBankMatchingRoute() {
                expect.step("schedule initial route");
            },
        },
        3046,
    );

    expect.verifySteps(["schedule initial route"]);
});

test("deliberate Bank Matching selection pushes a restorable history entry", () => {
    patchWithCleanup(router, {
        pushState(state, options) {
            expect.step(`push ${state.id}`);
            expect(options).toEqual({sync: true});
        },
    });
    patchWithCleanup(browser.history, {
        replaceState(state) {
            expect.step(`mark ${state.rebuildBankMatching}`);
        },
    });

    ReconcileController.prototype.updateURL.call(
        {
            initialLoad: false,
            rebuildHistorySelectionId: 3045,
            rebuildRestoringHistory: false,
        },
        3045,
    );

    expect.verifySteps(["mark true", "push 3045"]);
});

test("restored and automatic selections replace the current history entry", () => {
    patchWithCleanup(router, {
        replaceState(state, options) {
            expect.step(`replace ${state.id}`);
            expect(options).toEqual({sync: true});
        },
    });
    ReconcileController.prototype.updateURL.call(
        {
            initialLoad: false,
            rebuildHistorySelectionId: false,
            rebuildRestoringHistory: false,
        },
        3046,
    );

    expect.verifySteps(["replace 3046"]);
});

test("browser-restored selection does not write history again", () => {
    patchWithCleanup(router, {
        pushState() {
            expect.step("unexpected push");
        },
        replaceState() {
            expect.step("unexpected replacement");
        },
    });

    ReconcileController.prototype.updateURL.call(
        {
            initialLoad: false,
            rebuildHistorySelectionId: false,
            rebuildRestoringHistory: true,
        },
        3046,
    );

    expect.verifySteps([]);
});

test("Bank Matching history entries are explicitly identifiable", () => {
    expect(
        isBankMatchingHistoryEntry({
            rebuildBankMatching: true,
            skipRouteChange: true,
        }),
    ).toBe(true);
    expect(isBankMatchingHistoryEntry({skipRouteChange: true})).toBe(false);
});
