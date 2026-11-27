import { expect, test, beforeEach } from "@odoo/hoot";
import { defineProjectModels } from "@project/../tests/project_models";
import "@project/views/project_form/project_project_form_view";
import { ResPartner } from "@mail/../tests/mock_server/mock_models/res_partner";
import { browser } from "@web/core/browser/browser";
import {
    contains, mountView, mountViewInDialog, patchWithCleanup, onRpc,
} from "@web/../tests/web_test_helpers";

defineProjectModels();

beforeEach(() => {
    patchWithCleanup(ResPartner, { _records: [...ResPartner._records, { id: 17, name: "Atelier Aurore" }, { id: 18, name: "Atelier Ambre" }] });
});

const form = { type: "form", resModel: "res.partner", arch: '<form><field name="name"/></form>' };
const button = ".o_usl_copy_record_link";

test("copy the saved record's direct address", async () => {
    patchWithCleanup(navigator.clipboard, {
        async writeText(value) {
            expect.step(value);
        },
    });
    await mountView({ ...form, resId: 17 });
    await contains(button).click();
    expect.verifySteps([`${browser.location.origin}/odoo/res.partner/17`]);
    await expect(".o_notification").toHaveText(/Link copied/);
});

test("show the link for manual copying when the browser refuses", async () => {
    patchWithCleanup(navigator.clipboard, {
        async writeText() { throw new Error("Clipboard unavailable"); },
    });
    await mountView({ ...form, resId: 17 });
    await contains(button).click();
    await expect(".modal-title").toHaveText("Copy this link manually");
    expect(".modal-body input[readonly]").toHaveCount(1);
    const input = document.querySelector(".modal-body input");
    if (input) {
        expect(input).toHaveValue(`${browser.location.origin}/odoo/res.partner/17`);
        expect(input).toBeFocused();
        expect([input.selectionStart, input.selectionEnd]).toEqual([0, input.value.length]);
    }
});

test("new records have no link to copy", async () => {
    await mountView(form);
    expect(button).toHaveCount(0);
});

test("an embedded dialog does not offer the background form's link", async () => {
    await mountViewInDialog({ ...form, resId: 17 });
    expect(`.modal ${button}`).toHaveCount(0);
});


test("copying leaves unsaved edits in the current form", async () => {
    onRpc("web_save", () => expect.step("unexpected save"));
    patchWithCleanup(navigator.clipboard, { async writeText(value) { expect.step(value); } });
    await mountView({ ...form, resId: 17 });
    await contains('.o_field_widget[name="name"] input').edit("Atelier Aurore — à confirmer");
    await contains(button).click();
    expect(".o_form_status_indicator_buttons").not.toHaveClass("invisible");
    expect('.o_field_widget[name="name"] input').toHaveValue("Atelier Aurore — à confirmer");
    expect.verifySteps([`${browser.location.origin}/odoo/res.partner/17`]);
});

test("the link follows the current record after native pager navigation", async () => {
    patchWithCleanup(navigator.clipboard, { async writeText(value) { expect.step(value); } });
    await mountView({ ...form, resId: 17, resIds: [17, 18] });
    await contains(button).click();
    await contains(".o_pager_next").click();
    await contains(button).click();
    expect.verifySteps([`${browser.location.origin}/odoo/res.partner/17`, `${browser.location.origin}/odoo/res.partner/18`]);
});

test("a native specialized project form retains the same copy action", async () => {
    patchWithCleanup(navigator.clipboard, { async writeText(value) { expect.step(value); } });
    await mountView({ type: "form", resModel: "project.project", resId: 1, arch: '<form js_class="project_project_form"><field name="name"/></form>' });
    await contains(button).click();
    expect.verifySteps([`${browser.location.origin}/odoo/project.project/1`]);
});


test("the copy action supports keyboard activation", async () => {
    patchWithCleanup(navigator.clipboard, { async writeText(value) { expect.step(value); } });
    await mountView({ ...form, resId: 17 });
    await contains(button).focus();
    await contains(button).press("Enter");
    expect.verifySteps([`${browser.location.origin}/odoo/res.partner/17`]);
});

test("a principal form without breadcrumbs still offers one copy action", async () => {
    await mountView({ ...form, resId: 17, noBreadcrumbs: true });
    expect(button).toHaveCount(1);
});
