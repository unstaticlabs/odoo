import { expect, test } from "@odoo/hoot";
import { click } from "@odoo/hoot-dom";
import { animationFrame } from "@odoo/hoot-mock";
import { mountWithCleanup, patchWithCleanup } from "@web/../tests/web_test_helpers";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import { FieldDictation } from "@usl_voice/dictation";

defineMailModels();

test("a click on the microphone reaches no other listener and keeps the field focused", async () => {
    // Quick-create fields, list rows and popovers close on any click they see outside them.
    // If the microphone's click reached them, the field being dictated would disappear.
    patchWithCleanup(FieldDictation.prototype, {
        record() {
            expect.step("record");
        },
    });
    const input = document.createElement("input");
    document.body.append(input);
    const seen = (ev) => expect.step(`outside ${ev.type}`);
    window.addEventListener("click", seen, true);
    try {
        await mountWithCleanup(FieldDictation);
        input.focus();
        await animationFrame();
        await click(".o-usl-dictation-controls button");
        expect.verifySteps(["record"]);
        expect(input).toBeFocused();
        await click(document.body);
        expect.verifySteps(["outside click"]);
    } finally {
        window.removeEventListener("click", seen, true);
        input.remove();
    }
});

test("the words heard so far appear in the field being dictated, not beside the controls", async () => {
    const input = document.createElement("input");
    input.style.cssText = "position:fixed;left:50px;top:200px;width:400px";
    document.body.append(input);
    try {
        const dictation = await mountWithCleanup(FieldDictation);
        input.focus();
        await animationFrame();
        dictation.state.preview = "prepare the report";
        // While transcribing the words stay in place until the final text replaces them.
        dictation.setMode("processing");
        await animationFrame();
        await animationFrame();
        const words = document.querySelector(".o-usl-dictation-caption").getBoundingClientRect();
        const field = input.getBoundingClientRect();
        expect(words.left).toBeGreaterThan(field.left);
        expect(words.right).toBeLessThan(field.right);
        expect(words.top).toBeGreaterThan(field.top - 1);
        expect(words.bottom).toBeLessThan(field.bottom + 1);
        expect(".o-usl-dictation-controls .o-usl-dictation-caption").toHaveCount(0);
        dictation.cancel();
    } finally {
        input.remove();
    }
});
