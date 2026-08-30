import { expect, test } from "@odoo/hoot";
import { patchWithCleanup } from "@web/../tests/web_test_helpers";

import { browser } from "@web/core/browser/browser";
import {
    captureFeedbackScreenshot,
    feedbackPageContext,
    focusFeedbackComposer,
} from "../src/js/feedback_messaging_menu";

test("page context is typed and excludes browser location state", () => {
    patchWithCleanup(browser, {
        visualViewport: { width: 1439.6, height: 899.5 },
    });
    const context = feedbackPageContext({
        action: { id: 42 },
        props: {
            actionId: 99,
            resModel: "project.task",
            resId: 17,
            query: "token=secret",
        },
    });
    expect(context).toEqual({
        action_id: 42,
        model: "project.task",
        res_id: 17,
        viewport_width: 1440,
        viewport_height: 900,
    });
    expect(JSON.stringify(context)).not.toMatch(/location|query|token|fragment|hash/i);
});

test.tags("mobile");
test("narrow screens keep safe empty context defaults", () => {
    patchWithCleanup(browser, { visualViewport: { width: 390, height: 844 } });
    expect(feedbackPageContext(undefined)).toEqual({
        action_id: false,
        model: false,
        res_id: false,
        viewport_width: 390,
        viewport_height: 844,
    });
});

test("screenshot capture resizes, encodes, and always stops sharing", async () => {
    let stopped = false;
    const stream = { getTracks: () => [{ stop: () => (stopped = true) }] };
    const video = {
        muted: false,
        playsInline: false,
        videoWidth: 2560,
        videoHeight: 1440,
        play: () => Promise.resolve(),
        set onloadedmetadata(callback) {
            Promise.resolve().then(callback);
        },
    };
    const canvas = {
        width: 0,
        height: 0,
        getContext: () => ({ drawImage: () => {} }),
        toDataURL: () => "data:image/jpeg;base64,c2NyZWVuc2hvdA==",
    };
    const originalCreateElement = document.createElement.bind(document);
    patchWithCleanup(document, {
        createElement: (name) =>
            name === "video" ? video : name === "canvas" ? canvas : originalCreateElement(name),
    });
    const screenshot = await captureFeedbackScreenshot({
        getDisplayMedia: () => Promise.resolve(stream),
    });
    expect(screenshot.mimetype).toBe("image/jpeg");
    expect(screenshot.data).toBe("c2NyZWVuc2hvdA==");
    expect(screenshot.width).toBe(1920);
    expect(screenshot.height).toBe(1080);
    expect(stopped).toBe(true);
});

test("unsupported screenshot capture falls back without failing", async () => {
    expect(await captureFeedbackScreenshot({})).toBe(false);
});

test("cancelled screenshot capture remains a safe fallback", async () => {
    await expect(
        captureFeedbackScreenshot({
            getDisplayMedia: () => Promise.reject(new Error("capture cancelled")),
        })
    ).rejects.toThrow("capture cancelled");
});

test("refining opens the native Chatter composer before focusing it", async () => {
    let opened = false;
    let focused = false;
    const input = { focus: () => (focused = true) };
    const root = {
        querySelector(selector) {
            if (selector === ".o-mail-Chatter-sendMessage") {
                return { click: () => (opened = true) };
            }
            if (selector === ".o-mail-Composer-input") {
                return opened ? input : null;
            }
            return null;
        },
    };
    expect(await focusFeedbackComposer(root)).toBe(true);
    expect(opened).toBe(true);
    expect(focused).toBe(true);
});
