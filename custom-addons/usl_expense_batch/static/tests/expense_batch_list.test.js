import { expect, test } from "@odoo/hoot";

import {
    batchActionIsPrimary,
    canCreateExpenseBatch,
    refreshExpenseList,
} from "../src/js/expense_batch_list";
import { attentionIconClass } from "../src/js/expense_batch_attention_field";
import {
    activeQuickFilterName,
    quickFilterItemIds,
} from "../src/js/expense_batch_workspace";

function record(state, expenseBatchId = false) {
    return {
        data: {
            state,
            expense_batch_id: expenseBatchId,
        },
    };
}

test("batch creation accepts only unbatched eligible expense states", () => {
    expect(canCreateExpenseBatch([])).toBe(false);
    expect(canCreateExpenseBatch([record("draft")])).toBe(true);
    expect(canCreateExpenseBatch([record("approved")])).toBe(true);
    expect(canCreateExpenseBatch([record("posted")])).toBe(true);
    expect(
        canCreateExpenseBatch([
            record("draft"),
            record("approved"),
            record("posted"),
        ])
    ).toBe(true);

    for (const state of ["submitted", "in_payment", "paid", "refused"]) {
        expect(canCreateExpenseBatch([record(state)])).toBe(false);
    }
    expect(canCreateExpenseBatch([record("draft", [42, "Existing batch"])])).toBe(
        false
    );
});

test("batch assignment is the primary action for every eligible selection", () => {
    expect(batchActionIsPrimary([record("draft")])).toBe(true);
    expect(batchActionIsPrimary([record("draft"), record("draft")])).toBe(true);
    expect(batchActionIsPrimary([record("draft"), record("submitted")])).toBe(
        false
    );
});

test("closing the batch wizard reloads and renders the expense list", async () => {
    const controller = {
        model: {
            root: {
                async load() {
                    expect.step("load");
                },
            },
        },
        render(force) {
            expect.step(`render:${force}`);
        },
    };

    await refreshExpenseList(controller);

    expect.verifySteps(["load", "render:true"]);
});

test("attention indicator stays compact and distinguishes warnings from locks", () => {
    expect(attentionIconClass("warning")).toInclude("text-warning");
    expect(attentionIconClass("info")).toInclude("fa-lock");
});

test("batch quick filters expose one clear active workspace", () => {
    const searchItems = {
        1: { id: 1, name: "open_batches" },
        2: { id: 2, name: "needs_information" },
        3: { id: 3, name: "my_batches" },
        4: { id: 4, name: "exceptions" },
        5: { id: 5, name: "unrelated" },
    };

    expect(activeQuickFilterName(searchItems, [])).toBe("all");
    expect(activeQuickFilterName(searchItems, [{ searchItemId: 2 }])).toBe(
        "needs_information",
    );
    expect(quickFilterItemIds(searchItems)).toEqual([1, 2, 3, 4]);
});
