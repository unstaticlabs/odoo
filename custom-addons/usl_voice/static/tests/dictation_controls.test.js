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

test("a field can keep the microphone beside its label and release it on focus change", async () => {
    const form = document.createElement("form");
    const search = document.createElement("input");
    const slot = document.createElement("span");
    const other = document.createElement("input");
    slot.id = "dictation-slot-fixture";
    search.dataset.uslDictationSlot = slot.id;
    form.append(search, slot, other);
    document.body.append(form);
    try {
        await mountWithCleanup(FieldDictation);
        search.focus();
        await animationFrame();
        expect(slot.querySelector("[data-usl-action=record]")).not.toBe(null);
        other.focus();
        await animationFrame();
        expect(slot.childElementCount).toBe(0);
        expect(other).toBeFocused();
    } finally {
        form.remove();
    }
});
