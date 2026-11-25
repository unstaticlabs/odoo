import { expect, test } from "@odoo/hoot";
import { animationFrame } from "@odoo/hoot-mock";
import { mountWithCleanup } from "@web/../tests/web_test_helpers";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import { FieldDictation } from "@usl_voice/dictation";

defineMailModels();

test("the microphone counts as another overlay so quick-create and list rows stay open", async () => {
    // Kanban quick create and editable list rows close on a click outside their own
    // overlay. Without these markers, starting a dictation discards the field being dictated.
    const input = document.createElement("input");
    document.body.append(input);
    try {
        await mountWithCleanup(FieldDictation);
        input.focus();
        await animationFrame();
        expect(".o-usl-dictation-controls").toHaveClass("o-overlay-item");
        expect(".o-usl-dictation-controls").toHaveAttribute("data-list-ignore-click");
    } finally {
        input.remove();
    }
});
