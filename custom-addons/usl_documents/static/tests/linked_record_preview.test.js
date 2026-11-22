import { advanceTime, expect, test } from "@odoo/hoot";
import { animationFrame } from "@odoo/hoot-mock";
import { Component, xml } from "@odoo/owl";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import { contains, makeServerError, mountWithCleanup, patchWithCleanup } from "@web/../tests/web_test_helpers";
import { useOwnedDialogs } from "@web/core/utils/hooks";
import { LinkedRecordPreview } from "../src/linked_record_preview";

defineMailModels();

class ReadableForm extends Component {
    static template = xml`<span class="qa-readable-form">Synthetic project</span>`;
    static props = ["*"];
}

class PreviewHost extends Component {
    static template = xml`<button class="qa-preview" t-on-click="show">Preview</button>`;
    static props = { checkLink: Function, onUnavailable: Function };
    setup() {
        this.addDialog = useOwnedDialogs();
    }
    show() {
        this.addDialog(LinkedRecordPreview, {
            ...this.props, resModel: "project.project", resId: 31, title: "Linked record",
            readonly: true, preventEdit: true, preventCreate: true, canExpand: false,
        });
    }
}

test("an open preview clears the native form when access ends", async () => {
    patchWithCleanup(LinkedRecordPreview, { components: { ...LinkedRecordPreview.components, View: ReadableForm } });
    let readable = true;
    await mountWithCleanup(PreviewHost, { props: {
        checkLink: async () => {
            if (!readable) {
                throw makeServerError({ type: "AccessError" });
            }
            return { res_model: "project.project", res_id: 31 };
        },
        onUnavailable: () => expect.step("remove stale link"),
    } });
    await contains(".qa-preview").click();
    await animationFrame();
    expect(".qa-readable-form").toHaveText("Synthetic project");
    readable = false;
    await advanceTime(4100);
    await animationFrame();
    expect(".qa-readable-form").toHaveCount(0);
    expect(".modal [role=status]").toHaveText(/no longer have access/);
    expect.verifySteps(["remove stale link"]);
});

test("a stalled recheck clears an old preview and ignores its late response", async () => {
    patchWithCleanup(LinkedRecordPreview, { components: { ...LinkedRecordPreview.components, View: ReadableForm } });
    const action = { res_model: "project.project", res_id: 31 };
    let finish;
    let reachable = true;
    await mountWithCleanup(PreviewHost, { props: {
        checkLink: () => reachable ? Promise.resolve(action) : new Promise((resolve) => finish = resolve),
        onUnavailable: () => expect.step("remove link"),
    } });
    await contains(".qa-preview").click();
    await animationFrame();
    expect(".qa-readable-form").toHaveCount(1);
    reachable = false;
    await advanceTime(8100);
    await animationFrame();
    expect(".qa-readable-form").toHaveCount(0);
    expect(".modal [role=status]").toHaveText(/connection was interrupted/);
    finish(action);
    await animationFrame();
    expect(".qa-readable-form").toHaveCount(0);
    reachable = true;
    await contains(".modal button", { text: "Try again" }).click();
    await animationFrame();
    expect(".qa-readable-form").toHaveCount(1);
    expect.verifySteps([]);
});
