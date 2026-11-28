import { expect, test } from "@odoo/hoot";
import { resize } from "@odoo/hoot-dom";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import { contains, defineModels, fields, models, mountView, onRpc } from "@web/../tests/web_test_helpers";
import "../src/js/expense_receipt_email";

class Expense extends models.Model {
    _name = "hr.expense";
    name = fields.Char();
    receipt_email_url = fields.Char();
    _records = [
        { id: 1, name: "Atelier Lune", receipt_email_url: "/report/html/usl_accounting.expense_receipt_email/1" },
        { id: 2, name: "Atelier Ambre", receipt_email_url: "/report/html/usl_accounting.expense_receipt_email/2" },
    ];
}
defineMailModels();
defineModels([Expense]);

async function form(resId = 1, context = {}) {
    return mountView({
        type: "form", resModel: "hr.expense", resId, resIds: [1, 2], context,
        arch: `<form><sheet><div class="oe_button_box" name="button_box">
            <field name="receipt_email_url" widget="expense_receipt_email" class="oe_stat_button"
                readonly="1" nolabel="1" invisible="not id or not receipt_email_url"/>
            </div><field name="name" required="1"/></sheet></form>`,
    });
}

for (const width of [390, 1280]) {
    test(`receipt is a native link and preserves unsaved corrections at ${width}px`, async () => {
        await resize({ width, height: 844 });
        onRpc("hr.expense", "web_save", () => expect.step("unexpected expense save"));
        await form();
        await contains(".o_field_widget[name='name'] input").edit("Atelier Lune correction");
        if (document.querySelector(".o_button_more")) {
            await contains(".o_button_more").click();
        }
        const link = document.querySelector("[name='receipt_email_url'] a");
        expect(link).toHaveAttribute("href", "/report/html/usl_accounting.expense_receipt_email/1");
        expect(link).toHaveAttribute("target", "_blank");
        expect(link).toHaveAttribute("aria-label", "Receipt email");
        // Keep native browser navigation from leaving the mounted test; this
        // checks that the actual widget never cancels it or saves the expense.
        let nativeNavigation = false;
        link.addEventListener("click", (event) => {
            nativeNavigation = !event.defaultPrevented;
            event.preventDefault();
        });
        link.focus();
        expect(document.activeElement).toBe(link);
        await contains("[name='receipt_email_url'] a").click();
        expect(nativeNavigation).toBe(true);
        expect(".o_field_widget[name='name'] input").toHaveValue("Atelier Lune correction");
        expect.verifySteps([]);
    });
}

test("receipt link follows the selected record", async () => {
    await form();
    await contains(".o_pager_next").click();
    expect("[name='receipt_email_url'] a").toHaveAttribute("href", "/report/html/usl_accounting.expense_receipt_email/2");
});

test("new records and records without a readable source offer no receipt link", async () => {
    Expense._records[0].receipt_email_url = false;
    await form();
    expect("[name='receipt_email_url'] a").toHaveCount(0);
});

test("an unsaved expense has no receipt link even with a context default", async () => {
    await form(false, { default_receipt_email_url: "/report/html/usl_accounting.expense_receipt_email/1" });
    expect("[name='receipt_email_url'] a").toHaveCount(0);
});
