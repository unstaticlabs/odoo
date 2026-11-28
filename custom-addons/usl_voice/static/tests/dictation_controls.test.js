import { expect, test } from "@odoo/hoot";
import { click } from "@odoo/hoot-dom";
import { animationFrame } from "@odoo/hoot-mock";
import { mountWithCleanup, patchWithCleanup } from "@web/../tests/web_test_helpers";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import { FieldDictation } from "@usl_voice/dictation";
import { VoiceCapture } from "@usl_voice/capture";
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

test("the words heard so far appear in the field being dictated, where the text will go", async () => {
    // No microphone here: the recorder starts at once and no recognition is requested.
    patchWithCleanup(VoiceCapture.prototype, {
        async start() {},
    });
    patchWithCleanup(FieldDictation.prototype, {
        listen() {},
    });
    const input = document.createElement("textarea");
    input.style.cssText = "position:fixed;left:50px;top:200px;width:400px;height:80px;font:16px/20px monospace";
    const other = document.createElement("input");
    other.style.cssText = "position:fixed;left:50px;top:400px;width:400px";
    document.body.append(input, other);
    const words = () => document.querySelector(".o-usl-dictation-caption")?.getBoundingClientRect();
    const hear = async (dictation) => {
        dictation.state.preview = "prepare the report";
        dictation.settle();
        await animationFrame();
        await animationFrame();
        await animationFrame();
    };
    const start = async (dictation) => {
        input.value = "First line\nSecond line\nThird line";
        input.focus();
        input.setSelectionRange(10, 10); // end of the first line
        await animationFrame();
        await dictation.record();
        await hear(dictation);
    };
    try {
        const dictation = await mountWithCleanup(FieldDictation);
        await start(dictation);
        const field = input.getBoundingClientRect();
        const first = words();
        expect(first.left).toBeGreaterThan(field.left);
        expect(first.right).toBeLessThan(field.right);
        expect(first.top).toBeGreaterThan(field.top - 1);
        expect(first.bottom).toBeLessThan(field.top + 30);
        // Nothing is typed before the end.
        expect(input.value).toBe("First line\nSecond line\nThird line");

        // The insertion point moves two lines down: the text still goes to the first line, and so do the words.
        input.setSelectionRange(input.value.length, input.value.length);
        await hear(dictation);
        expect(Math.round(words().top)).toBe(Math.round(first.top));
        expect(Math.round(words().left)).toBe(Math.round(first.left));

        // Typing in the field means the text will not be inserted: the words leave it.
        input.dispatchEvent(new InputEvent("input", { bubbles: true }));
        await hear(dictation);
        expect(".o-usl-dictation-caption").toHaveCount(0);
        dictation.cancel();
        await animationFrame();

        // Moving to another field: the text will not go there, so neither do the words.
        await start(dictation);
        expect(".o-usl-dictation-caption").toHaveCount(1);
        other.focus();
        await hear(dictation);
        expect(".o-usl-dictation-caption").toHaveCount(0);
        dictation.cancel();
        await animationFrame();

        // A one-line field scrolled sideways: once the saved place is out of sight, so are the words.
        const line = document.createElement("input");
        line.style.cssText = "position:fixed;left:50px;top:500px;width:120px;font:16px monospace";
        document.body.append(line);
        try {
            line.value = "A sentence much longer than the field that holds it, by far";
            line.focus();
            line.setSelectionRange(line.value.length, line.value.length);
            line.scrollLeft = line.scrollWidth;
            await animationFrame();
            await dictation.record();
            await hear(dictation);
            expect(".o-usl-dictation-caption").toHaveCount(1);
            line.setSelectionRange(0, 0);
            line.scrollLeft = 0;
            await hear(dictation);
            expect(".o-usl-dictation-caption").toHaveCount(0);
            // A field that changes width lays its text out again: the saved place no longer holds.
            line.scrollLeft = line.scrollWidth;
            await hear(dictation);
            expect(".o-usl-dictation-caption").toHaveCount(1);
            line.style.width = "200px";
            await hear(dictation);
            expect(".o-usl-dictation-caption").toHaveCount(0);
            dictation.cancel();
            await animationFrame();
        } finally {
            line.remove();
        }

        // Cancelling removes them.
        await start(dictation);
        expect(".o-usl-dictation-caption").toHaveCount(1);
        dictation.cancel();
        await animationFrame();
        await animationFrame();
        expect(".o-usl-dictation-caption").toHaveCount(0);
    } finally {
        input.remove();
        other.remove();
    }
});
