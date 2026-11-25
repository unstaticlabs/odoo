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
