import { expect, test } from "@odoo/hoot";
import { advanceTime, animationFrame } from "@odoo/hoot-mock";
import { mountWithCleanup } from "@web/../tests/web_test_helpers";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import { VoiceMeter, formatSeconds } from "@usl_voice/meter";

defineMailModels();

test("elapsed time reads as minutes and seconds", () => {
    expect(formatSeconds(0)).toBe("0:00");
    expect(formatSeconds(9)).toBe("0:09");
    expect(formatSeconds(75)).toBe("1:15");
});

test("the meter follows the recording and counts down its last thirty seconds", async () => {
    const capture = { elapsed: 4, level: () => 0.5 };
    await mountWithCleanup(VoiceMeter, { props: { capture } });
    await advanceTime(300);
    await animationFrame();
    expect(".o-usl-voice-meter").toHaveText("0:04");
    expect(document.querySelector(".o-usl-voice-dot").style.getPropertyValue("--usl-voice-level")).toBe("0.50");
    capture.elapsed = 285;
    await advanceTime(300);
    await animationFrame();
    expect(".o-usl-voice-meter").toHaveText("0:15 left");
});
