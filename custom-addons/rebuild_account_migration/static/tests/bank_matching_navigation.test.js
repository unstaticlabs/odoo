import {expect, test} from "@odoo/hoot";
import {patchWithCleanup} from "@web/../tests/web_test_helpers";

import {router} from "@web/core/browser/router";
import {ReconcileController} from "@account_reconcile_oca/views/reconcile_kanban/reconcile_controller.esm";
import "../src/js/reconcile_navigation";

test("automatic Bank Matching selection preserves the previous history entry", () => {
    patchWithCleanup(router, {
        replaceState() {
            expect.step("route replaced");
        },
    });

    ReconcileController.prototype.updateURL.call(
        {initialLoad: true},
        3046,
    );

    expect.verifySteps([]);
});

test("later Bank Matching selections update only the current history entry", () => {
    patchWithCleanup(router, {
        replaceState(state, options) {
            expect.step(`selected ${state.id}`);
            expect(options).toEqual({sync: true});
        },
    });

    ReconcileController.prototype.updateURL.call(
        {initialLoad: false},
        3045,
    );

    expect.verifySteps(["selected 3045"]);
});
