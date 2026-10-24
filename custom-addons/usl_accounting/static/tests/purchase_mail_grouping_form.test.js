import { expect, test } from "@odoo/hoot";
import { resize } from "@odoo/hoot-dom";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import { contains, defineModels, fields, models, mountView, onRpc } from "@web/../tests/web_test_helpers";
import "@account/services/account_move_service";
import "../src/js/purchase_mail_grouping_form";

class AccountMove extends models.Model {
    _name = "account.move";
    name = fields.Char();
    invoice_date = fields.Date();
    move_type = fields.Selection({ selection: [["in_invoice", "Vendor Bill"], ["in_refund", "Vendor Credit Note"], ["out_invoice", "Customer Invoice"]] });
    _records = [{ id: 1, name: "[QA] Imported draft", invoice_date: false, move_type: "in_invoice" }];
    get_extra_print_items() { return []; }
}
defineMailModels();
defineModels({ AccountMove });

async function billForm({ action = "action_review_mail_grouping", resId = 1 } = {}) {
    return mountView({
        type: "form", resModel: "account.move", resId,
        arch: `<form js_class="account_move_form">
            <header><button name="${action}" type="object" string="Review attachments"/></header>
            <sheet><field name="move_type" invisible="1"/><field name="name"/><field name="invoice_date" required="1"/></sheet>
        </form>`,
    });
}

for (const width of [390, 1280]) {
    test(`saved clean purchase draft opens grouping before its date is known at ${width}px`, async () => {
        await resize({ width, height: 844 });
        onRpc("account.move", "action_review_mail_grouping", ({ args }) => {
            expect(args[0]).toEqual([1]);
            expect.step("native review action");
            return false;
        });
        onRpc("account.move", "web_save", () => expect.step("unexpected header save"));
        await billForm();
        await contains("button[name='action_review_mail_grouping']").click();
        expect(".o_field_widget[name='invoice_date'] input").toHaveValue("");
        expect.verifySteps(["native review action"]);
    });
}

test("saved purchase credit note opens the same native grouping action without header save", async () => {
    AccountMove._records[0].move_type = "in_refund";
    onRpc("account.move", "action_review_mail_grouping", () => { expect.step("native review action"); return false; });
    onRpc("account.move", "web_save", () => expect.step("unexpected header save"));
    await billForm();
    await contains("button[name='action_review_mail_grouping']").click();
    expect.verifySteps(["native review action"]);
});

for (const state of ["dirty", "unblurred", "new", "financial", "sales", "other_action"]) {
    test(`${state} retains native save validation and pending edits`, async () => {
        if (state === "sales") { AccountMove._records[0].move_type = "out_invoice"; }
        const action = state === "financial" ? "action_post" : state === "other_action" ? "action_open_journal_items" : "action_review_mail_grouping";
        onRpc("account.move", action, () => expect.step("unexpected action"));
        onRpc("account.move", "web_save", () => expect.step("unexpected header write"));
        await billForm({ action, resId: state === "new" ? false : 1 });
        if (state === "dirty") {
            await contains(".o_field_widget[name='name'] input").edit("[QA] Keep pending correction");
        } else if (state === "unblurred") {
            const input = document.querySelector(".o_field_widget[name='name'] input");
            input.value = "[QA] Keep pending correction";
            input.dispatchEvent(new Event("input", { bubbles: true }));
        }
        await contains(`button[name='${action}']`).click();
        expect(".o_field_widget[name='invoice_date']").toHaveClass("o_field_invalid");
        if (["dirty", "unblurred"].includes(state)) {
            expect(".o_field_widget[name='name'] input").toHaveValue("[QA] Keep pending correction");
        }
        expect.verifySteps([]);
    });
}
