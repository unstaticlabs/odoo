import { beforeEach, expect, test } from "@odoo/hoot";
import { browser } from "@web/core/browser/browser";
import { patchTranslations, patchWithCleanup } from "@web/../tests/web_test_helpers";
import { openLinkedReceipt } from "../src/js/linked_receipt_handoff";

const action = { params: { retrieval_id: 11, generation: 2 } };
beforeEach(() => patchTranslations());
function environment() {
    return { services: { notification: { add(message) { expect.step(String(message)); } } } };
}

function popup() {
    return {
        closed: false,
        opener: {},
        document: document.implementation.createHTMLDocument("[QA] Receipt"),
        close() { expect.step("close"); },
    };
}

test("one native action opens one tab and submits one authenticated internal POST", () => {
    const tab = popup();
    patchWithCleanup(odoo, { csrf_token: "qa-csrf-fixture" });
    patchWithCleanup(browser, { open(url, target) {
        expect(url).toBe("about:blank");
        expect(target).toBe("_blank");
        expect.step("open");
        return tab;
    } });
    patchWithCleanup(HTMLFormElement.prototype, { submit() {
        expect(this.method).toBe("post");
        expect(new URL(this.action).pathname).toBe("/usl/expenses/linked-receipt/11/continue");
        expect(this.target).toBe("_self");
        expect(Object.fromEntries(new FormData(this))).toEqual({
            csrf_token: "qa-csrf-fixture", generation: "2",
        });
        expect(tab.opener).toBe(null);
        expect.step("post");
    } });
    openLinkedReceipt(environment(), action);
    expect(tab.document.querySelector("form")).toBe(null);
    expect.verifySteps(["open", "post"]);
});

test("blocked popup consumes no handoff and offers recovery", () => {
    patchWithCleanup(odoo, { csrf_token: "qa-csrf-fixture" });
    patchWithCleanup(browser, { open() { return null; } });
    patchWithCleanup(HTMLFormElement.prototype, { submit() { expect.step("unexpected-post"); } });
    openLinkedReceipt(environment(), action);
    expect.verifySteps(["Allow popups for Odoo, then open the receipt website again."]);
});

test("browser submission failure closes the blank tab and preserves an explicit retry", () => {
    const tab = popup();
    patchWithCleanup(odoo, { csrf_token: "qa-csrf-fixture" });
    patchWithCleanup(browser, { open() { return tab; } });
    patchWithCleanup(HTMLFormElement.prototype, { submit() { throw new Error("[QA] Browser failure"); } });
    openLinkedReceipt(environment(), action);
    expect(tab.document.querySelector("form")).toBe(null);
    expect.verifySteps(["close", "The receipt website could not be opened. Try again from the expense."]);
});

test("missing CSRF or malformed action opens no tab", () => {
    patchWithCleanup(odoo, { csrf_token: false });
    patchWithCleanup(browser, { open() { expect.step("unexpected-open"); } });
    openLinkedReceipt(environment(), action);
    expect.verifySteps(["Reload Odoo, then open the receipt website again."]);
});
