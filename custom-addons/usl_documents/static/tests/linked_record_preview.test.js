import { advanceTime, expect, test } from "@odoo/hoot";
import { animationFrame } from "@odoo/hoot-mock";
import { Component, xml } from "@odoo/owl";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import { contains, makeServerError, mountWithCleanup } from "@web/../tests/web_test_helpers";
import { useOwnedDialogs } from "@web/core/utils/hooks";
import { LinkedRecordPreview } from "../src/linked_record_preview";

defineMailModels();

const snapshot = {
    res_model: "project.project", res_id: 31, name: "Synthetic project",
    fields: [{ label: "Company", value: "Demo company" }],
};

class PreviewHost extends Component {
    static template = xml`<button class="qa-preview" t-on-click="show">Preview</button>`;
    static props = { checkLink: Function, onUnavailable: Function };
    setup() {
        this.addDialog = useOwnedDialogs();
    }
    show() {
        this.addDialog(LinkedRecordPreview, {
            ...this.props, resModel: "project.project", resId: 31, title: "Linked record",
        });
    }
}

test("an open preview clears the record details when access ends", async () => {
    let readable = true;
    await mountWithCleanup(PreviewHost, { props: {
        checkLink: async () => {
            if (!readable) {
                throw makeServerError({ type: "AccessError" });
            }
            return snapshot;
        },
        onUnavailable: () => expect.step("remove stale link"),
    } });
    await contains(".qa-preview").click();
    await animationFrame();
    expect(".o_usl_linked_record_summary h3").toHaveText("Synthetic project");
    expect(".o_usl_linked_record_summary dd").toHaveText("Demo company");
    expect(".modal-body :is(button, input, a, [role=button], [contenteditable])").toHaveCount(0);
    readable = false;
    await advanceTime(4100);
    await animationFrame();
    expect(".o_usl_linked_record_summary").toHaveCount(0);
    expect(".modal [role=status]").toHaveText(/no longer have access/);
    expect.verifySteps(["remove stale link"]);
});

test("a stalled recheck clears an old preview and ignores its late response", async () => {
    const action = snapshot;
    let finish;
    let reachable = true;
    await mountWithCleanup(PreviewHost, { props: {
        checkLink: () => reachable ? Promise.resolve(action) : new Promise((resolve) => finish = resolve),
        onUnavailable: () => expect.step("remove link"),
    } });
    await contains(".qa-preview").click();
    await animationFrame();
    expect(".o_usl_linked_record_summary").toHaveCount(1);
    reachable = false;
    await advanceTime(8100);
    await animationFrame();
    expect(".o_usl_linked_record_summary").toHaveCount(0);
    expect(".modal [role=status]").toHaveText(/connection was interrupted/);
    finish(action);
    await animationFrame();
    expect(".o_usl_linked_record_summary").toHaveCount(0);
    reachable = true;
    await contains(".modal button", { text: "Try again" }).click();
    await animationFrame();
    expect(".o_usl_linked_record_summary").toHaveCount(1);
    expect.verifySteps([]);
});


test("a server failure hides the record and a retry recovers it without dropping the link", async () => {
    let failing = false;
    await mountWithCleanup(PreviewHost, { props: {
        checkLink: async () => {
            if (failing) {
                throw makeServerError({ type: "RuntimeError" });
            }
            return snapshot;
        },
        onUnavailable: () => expect.step("remove link"),
    } });
    await contains(".qa-preview").click();
    await animationFrame();
    failing = true;
    await advanceTime(4100);
    await animationFrame();
    expect(".o_usl_linked_record_summary").toHaveCount(0);
    expect(".modal [role=status]").toHaveText(/could not be loaded/);
    failing = false;
    await contains(".modal button", { text: "Try again" }).click();
    await animationFrame();
    expect(".o_usl_linked_record_summary h3").toHaveText("Synthetic project");
    expect.verifySteps([]);
});
