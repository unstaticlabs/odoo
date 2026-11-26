import { expect, test } from "@odoo/hoot";
import { click } from "@odoo/hoot-dom";
import { animationFrame } from "@odoo/hoot-mock";
import { mountWithCleanup, patchWithCleanup } from "@web/../tests/web_test_helpers";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import { FieldDictation } from "@usl_voice/dictation";
import { captureTarget } from "@usl_voice/dictation_target";

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

test("retry returns to the unchanged field before the retry button disappears", async () => {
    patchWithCleanup(FieldDictation.prototype, {
        async transcribe() {
            await animationFrame();
            return "Heard";
        },
    });
    const input = document.createElement("input");
    document.body.append(input);
    try {
        const controls = await mountWithCleanup(FieldDictation, { noMainContainer: true });
        input.focus();
        controls.destination = captureTarget(input);
        controls.pendingAudio = new File(["synthetic"], "voice.mp3");
        controls.setMode("retry");
        await animationFrame();
        document.querySelector('[data-usl-action="retry"]').focus();
        await click('[data-usl-action="retry"]');
        await animationFrame();
        await animationFrame();
        expect(input.value).toBe("Heard");
        expect(input).toBeFocused();
    } finally {
        input.remove();
    }
});
