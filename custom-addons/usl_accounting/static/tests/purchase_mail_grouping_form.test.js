import { expect, test } from "@odoo/hoot";
import { resize } from "@odoo/hoot-dom";
import { Deferred } from "@odoo/hoot-mock";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import { clickSave, contains, defineModels, fields, models, mountView, onRpc } from "@web/../tests/web_test_helpers";
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
class PurchaseReview extends models.Model {
    _name = "usl.purchase.mail.grouping.review";
    line_ids = fields.One2many({ relation: "usl.purchase.mail.grouping.review.line", relation_field: "review_id" });
    _records = [{ id: 1, line_ids: [1, 2, 3] }];
}
class PurchaseLine extends models.Model {
    _name = "usl.purchase.mail.grouping.review.line";
    review_id = fields.Many2one({ relation: "usl.purchase.mail.grouping.review" });
    name = fields.Char();
    raw = fields.Binary();
    use = fields.Selection({ selection: [["supporting", "Supporting file"], ["main", "Main document"], ["separate", "Another invoice"]] });
    _records = [
        { id: 1, review_id: 1, name: "First.pdf", use: "main" },
        { id: 2, review_id: 1, name: "Second.pdf", use: "supporting" },
        { id: 3, review_id: 1, name: "Other.pdf", use: "separate" },
    ];
}
defineModels({ AccountMove, PurchaseReview, PurchaseLine });

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

for (const width of [390, 1280]) {
    test(`choosing a new main file clears the previous main and preserves separate choices at ${width}px`, async () => {
        await resize({ width, height: 844 });
        await mountView({
            type: "form", resModel: "usl.purchase.mail.grouping.review", resId: 1,
            arch: `<form><field name="line_ids"><list editable="bottom" create="0" delete="0">
                <field name="name"/><field name="use" widget="usl_purchase_attachment_use"/>
            </list></field></form>`,
        });
        await contains(".o_data_row:nth-child(2) [name='use']").click();
        await contains(".o_data_row:nth-child(2) [name='use'] .o_select_menu_toggler").click();
        await contains(".o_select_menu_item:contains(Main document)").click();
        await contains(".o_form_view").click();
        expect(".o_data_row:nth-child(1) [name='use']").toHaveText("Supporting file");
        expect(".o_data_row:nth-child(2) [name='use']").toHaveText("Main document");
        expect(".o_data_row:nth-child(3) [name='use']").toHaveText("Another invoice");
        onRpc("usl.purchase.mail.grouping.review", "web_save", ({ args }) => {
            const updates = args[1].line_ids.filter(([command]) => command === 1);
            expect(updates).toEqual([[1, 1, { use: "supporting" }], [1, 2, { use: "main" }]]);
            expect.step("exclusive choices saved");
        });
        await clickSave();
        expect.verifySteps(["exclusive choices saved"]);
    });
}


test("immediate Confirm flushes the selected main after a delayed parent onchange", async () => {
    PurchaseReview._onChanges = { line_ids() {} };
    const delayed = new Deferred();
    let calls = 0;
    onRpc("usl.purchase.mail.grouping.review", "onchange", async () => {
        if (++calls === 1) {
            expect.step("old main update pending");
            await delayed;
        }
    });
    onRpc("usl.purchase.mail.grouping.review", "web_save", ({ args }) => {
        expect(args[1].line_ids.filter(([command]) => command === 1)).toEqual([
            [1, 1, { use: "supporting" }], [1, 2, { use: "main" }],
        ]);
        expect.step("both choices saved");
    });
    onRpc("usl.purchase.mail.grouping.review", "action_keep_together", () => {
        expect.step("confirm");
        return false;
    });
    await mountView({
        type: "form", resModel: "usl.purchase.mail.grouping.review", resId: 1,
        arch: `<form><header><button name="action_keep_together" type="object" string="Confirm"/></header>
            <field name="line_ids"><list editable="bottom" create="0" delete="0">
                <field name="name"/><field name="use" widget="usl_purchase_attachment_use"/>
            </list></field></form>`,
    });
    await contains(".o_data_row:nth-child(2) [name='use']").click();
    await contains(".o_data_row:nth-child(2) [name='use'] .o_select_menu_toggler").click();
    await contains(".o_select_menu_item:contains(Main document)").click();
    await expect.waitForSteps(["old main update pending"]);
    await contains("button[name='action_keep_together']").click();
    delayed.resolve();
    await expect.waitForSteps(["both choices saved", "confirm"]);
});


for (const width of [320, 390]) {
    test(`phone file choices stay fully visible after editing without horizontal scroll at ${width}px`, async () => {
        await resize({ width, height: 844 });
        PurchaseLine._records.forEach((record, index) => {
            record.raw = "4";
            record.name = ["justificatif.pdf", "autre-facture.pdf", "document-principal.pdf"][index];
        });
        await mountView({
            type: "form", resModel: "usl.purchase.mail.grouping.review", resId: 1,
            arch: `<form><div style="width:${width - 32}px"><field name="line_ids" class="o_purchase_attachment_choices" nolabel="1">
                <list editable="bottom" create="0" delete="0">
                    <field name="name" column_invisible="1"/>
                    <field name="raw" widget="binary" filename="name" readonly="1"/>
                    <field name="use" widget="usl_purchase_attachment_use" width="180px"/>
                </list></field></div></form>`,
        });
        await contains(".o_data_row:nth-child(3) [name='use']").click();
        await contains(".o_data_row:nth-child(3) [name='use'] .o_select_menu_toggler").click();
        await contains(".o_select_menu_item:contains(Main document)").click();
        await contains(".o_data_row:nth-child(2) [name='use']").click();
        await contains(".o_data_row:nth-child(2) [name='use'] .o_select_menu_toggler").click();
        await contains(".o_select_menu_item:contains(Another invoice)").click();
        await contains(".o_form_view").click();
        const renderer = document.querySelector(".o_purchase_attachment_choices .o_list_renderer");
        expect(renderer.scrollWidth).toBeLessThan(renderer.clientWidth + 2);
        expect(renderer.scrollLeft).toBe(0);
        const bounds = renderer.getBoundingClientRect();
        for (const cell of renderer.querySelectorAll("th[data-name], .o_data_cell[name]")) {
            expect(cell.getBoundingClientRect().left).toBeGreaterThan(bounds.left - 2);
            expect(cell.getBoundingClientRect().right).toBeLessThan(bounds.right + 2);
            expect(cell.scrollWidth).toBeLessThan(cell.clientWidth + 2);
        }
        expect(".o_data_row:nth-child(3) [name='raw']").toHaveText("document-principal.pdf");
        expect(".o_data_row:nth-child(3) [name='use']").toHaveText("Main document");
    });
}
