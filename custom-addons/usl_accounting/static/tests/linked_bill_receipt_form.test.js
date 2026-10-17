import { expect, test } from "@odoo/hoot";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import { AttachDocumentWidget } from "@web/views/widgets/attach_document/attach_document";
import { contains, defineModels, fields, models, mountView, onRpc, patchWithCleanup } from "@web/../tests/web_test_helpers";
import "@account/services/account_move_service";
import { LinkedBillReceiptFormController } from "../src/js/linked_bill_receipt_form";

class AccountMove extends models.Model {
    _name = "account.move";
    name = fields.Char();
    invoice_date = fields.Date();
    move_type = fields.Selection({ selection: [["in_invoice", "Vendor Bill"], ["out_invoice", "Customer Invoice"]] });
    _records = [{ id: 1, name: "[QA] Imported bill without its original", invoice_date: false, move_type: "in_invoice" }];
    get_extra_print_items() { return []; }
}
defineMailModels();
defineModels({ AccountMove });

async function billForm(action, { resId = 1, upload = false } = {}) {
    return mountView({
        type: "form", resModel: "account.move", resId,
        arch: `<form js_class="usl_linked_bill_receipt_form">
            <header><button name="${action}" type="object" string="Recover document"/></header>
            <sheet><field name="move_type" invisible="1"/><field name="name"/><field name="invoice_date" required="1"/>
                ${upload ? '<widget name="usl_linked_bill_receipt_upload" string="Upload downloaded document" action="action_attach_linked_receipt"/>' : ''}
            </sheet>
        </form>`,
    });
}

for (const action of [
    "action_accept_linked_receipt", "action_attach_linked_receipt",
    "action_dismiss_linked_receipt", "action_open_linked_receipt_website",
    "action_retry_linked_receipt", "action_review_linked_receipt",
    "action_scan_existing_receipt_emails",
]) {
    test(`saved clean bill can ${action} before its date is known`, async () => {
        onRpc("account.move", action, ({ args }) => {
            expect(args[0]).toEqual([1]);
            expect.step("native receipt RPC");
            return false;
        });
        onRpc("account.move", "web_save", () => expect.step("unexpected bill save"));
        await billForm(action);
        await contains(`button[name='${action}']`).click();
        expect.verifySteps(["native receipt RPC"]);
        expect(".o_field_widget[name='invoice_date'] input").toHaveValue("");
    });
}

test("incomplete dirty bill retains its edits and native validation before recovery", async () => {
    onRpc("account.move", "action_accept_linked_receipt", () => expect.step("unexpected recovery"));
    onRpc("account.move", "web_save", () => expect.step("unexpected write"));
    await billForm("action_accept_linked_receipt");
    await contains(".o_field_widget[name='name'] input").edit("[QA] Unsaved bill correction");
    await contains("button[name='action_accept_linked_receipt']").click();
    expect(".o_field_widget[name='name'] input").toHaveValue("[QA] Unsaved bill correction");
    expect(".o_field_widget[name='invoice_date']").toHaveClass("o_field_invalid");
    expect.verifySteps([]);
});

test("new incomplete bill keeps native save validation before recovery", async () => {
    onRpc("account.move", "action_accept_linked_receipt", () => expect.step("unexpected recovery"));
    onRpc("account.move", "web_save", () => expect.step("unexpected write"));
    await billForm("action_accept_linked_receipt", { resId: false });
    await contains("button[name='action_accept_linked_receipt']").click();
    expect(".o_field_widget[name='invoice_date']").toHaveClass("o_field_invalid");
    expect.verifySteps([]);
});

test("financial action keeps native required-date validation on a clean saved bill", async () => {
    onRpc("account.move", "action_post", () => expect.step("unexpected posting"));
    onRpc("account.move", "web_save", () => expect.step("unexpected write"));
    await billForm("action_post");
    await contains("button[name='action_post']").click();
    expect(".o_field_widget[name='invoice_date']").toHaveClass("o_field_invalid");
    expect.verifySteps([]);
});

test("native source-first upload opens before the saved bill date is known", async () => {
    patchWithCleanup(HTMLInputElement.prototype, { click() {
        expect(this.type).toBe("file");
        expect.step("native file picker");
    } });
    onRpc("account.move", "web_save", () => expect.step("unexpected write"));
    await billForm("action_accept_linked_receipt", { upload: true });
    await contains("button.o_attachment_button").click();
    expect.verifySteps(["native file picker"]);
    expect(".o_field_widget[name='invoice_date'] input").toHaveValue("");
});

test("dirty bill upload retains edits and native required-date validation", async () => {
    patchWithCleanup(HTMLInputElement.prototype, { click() { expect.step("unexpected file picker"); } });
    await billForm("action_accept_linked_receipt", { upload: true });
    await contains(".o_field_widget[name='name'] input").edit("[QA] Unsaved correction before upload");
    await contains("button.o_attachment_button").click();
    expect(".o_field_widget[name='name'] input").toHaveValue("[QA] Unsaved correction before upload");
    expect(".o_field_widget[name='invoice_date']").toHaveClass("o_field_invalid");
    expect.verifySteps([]);
});

test("expense upload retains the original native save contract", async () => {
    const record = { resModel: "hr.expense", resId: 1, isNew: false,
        async isDirty() { return false; },
        async save() { expect.step("native expense save"); return false; },
    };
    expect(await AttachDocumentWidget.prototype.beforeOpen.call({ props: { record } })).toBe(false);
    expect.verifySteps(["native expense save"]);
});


for (const state of ["clean", "dirty", "new", "new_dirty", "unblurred"]) {
    test(`${state} missing-date bill preserves native navigation and unload behavior`, async () => {
        let controller;
        patchWithCleanup(LinkedBillReceiptFormController.prototype, {
            setup() { super.setup(...arguments); controller = this; },
        });
        onRpc("account.move", "web_save", () => expect.step("unexpected write"));
        await billForm("action_accept_linked_receipt", { resId: state.startsWith("new") ? false : 1 });
        if (state === "dirty" || state === "new_dirty") {
            await contains(".o_field_widget[name='name'] input").edit("[QA] Keep these unsaved changes");
        }
        if (state === "unblurred") {
            const input = document.querySelector(".o_field_widget[name='name'] input");
            input.value = "[QA] Keep unblurred changes";
            input.dispatchEvent(new Event("input", { bubbles: true }));
            expect(input.value).toBe("[QA] Keep unblurred changes");
        }
        const shouldProtect = ["dirty", "new_dirty", "unblurred"].includes(state);
        const event = { prevented: false, preventDefault() { this.prevented = true; }, returnValue: undefined };
        await controller.beforeUnload(event);
        expect(event.prevented).toBe(shouldProtect);
        if (!shouldProtect) {
            expect(event.returnValue).toBe(undefined);
            await controller.beforeLeave();
            expect(await controller.model.root.isDirty()).toBe(false);
        } else {
            expect(event.returnValue).toBe("Unsaved changes");
            expect(controller.model.root.isValid).toBe(false);
        }
        if (state === "dirty" || state === "new_dirty") {
            expect(".o_field_widget[name='name'] input").toHaveValue("[QA] Keep these unsaved changes");
        }
        if (state === "unblurred") {
            expect(controller.model.root.data.name).toBe("[QA] Keep unblurred changes");
        }
        expect.verifySteps([]);
    });
}


test("customer invoice keeps its native missing-date unload behavior", async () => {
    AccountMove._records[0].move_type = "out_invoice";
    let controller;
    patchWithCleanup(LinkedBillReceiptFormController.prototype, {
        setup() { super.setup(...arguments); controller = this; },
    });
    onRpc("account.move", "web_save", () => expect.step("unexpected write"));
    await billForm("action_post");
    const event = { prevented: false, preventDefault() { this.prevented = true; } };
    await controller.beforeUnload(event);
    expect(event.prevented).toBe(true);
    expect(event.returnValue).toBe("Unsaved changes");
    expect.verifySteps([]);
});
