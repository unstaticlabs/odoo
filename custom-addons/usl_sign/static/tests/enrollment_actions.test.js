import {expect, test} from "@odoo/hoot";
import {browser} from "@web/core/browser/browser";
import {patchWithCleanup} from "@web/../tests/web_test_helpers";

import {copyText} from "../src/js/enrollment_actions.esm";

test("copyText writes the setup link to the browser clipboard", async () => {
    patchWithCleanup(browser.navigator.clipboard, {
        async writeText(value) {
            expect.step(`copied: ${value}`);
        },
    });

    await copyText("https://sign.example.test/setup/private-token");

    expect.verifySteps(["copied: https://sign.example.test/setup/private-token"]);
});

test("copyText falls back when clipboard permission is unavailable", async () => {
    patchWithCleanup(browser.navigator.clipboard, {
        async writeText() {
            throw new Error("Clipboard permission denied");
        },
    });
    patchWithCleanup(document, {
        execCommand(command) {
            expect.step(command);
            return true;
        },
    });

    await copyText("http://private-sign.example.test/setup/private-token");

    expect.verifySteps(["copy"]);
});
