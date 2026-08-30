import { expect, test } from "@odoo/hoot";
import { setInputFiles } from "@odoo/hoot-dom";
import { animationFrame } from "@odoo/hoot-mock";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";
import {
    contains,
    mountWithCleanup,
    onRpc,
    patchWithCleanup,
} from "@web/../tests/web_test_helpers";

import { browser } from "@web/core/browser/browser";
import {
    captureFeedbackScreenshot,
    FeedbackPanel,
    feedbackPageContext,
    focusFeedbackComposer,
} from "../src/js/feedback_messaging_menu";

defineMailModels();

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

test("draft previews a default-selected screenshot and removes it explicitly", async () => {
    onRpc("usl.feedback.submission", "feedback_start", () => {
        expect.step("start");
        return {
            draft_id: 41,
            company_name: "Unstatic Labs",
            context_available: true,
            recent: [],
        };
    });
    onRpc("usl.feedback.submission", "feedback_add_attachment", ({ args }) => {
        expect(args.slice(1)).toEqual([
            "screen.jpg",
            "image/jpeg",
            "c2NyZWVuc2hvdA==",
            true,
        ]);
        expect(args.at(-1)).toBe(true);
        expect.step("upload screenshot");
        return { id: 73, name: "screen.jpg", mimetype: "image/jpeg" };
    });
    onRpc("usl.feedback.submission", "feedback_remove_attachment", ({ args }) => {
        expect(args[1]).toBe(73);
        expect.step("remove screenshot");
        return true;
    });
    await mountWithCleanup(FeedbackPanel, {
        props: {
            close() {},
            pageContext: { action_id: 7, model: "project.task", res_id: 9 },
            screenshot: {
                name: "screen.jpg",
                mimetype: "image/jpeg",
                data: "c2NyZWVuc2hvdA==",
                previewUrl: "data:image/jpeg;base64,c2NyZWVuc2hvdA==",
                width: 1440,
                height: 900,
            },
            captureError: false,
        },
    });
    expect(".o-usl-FeedbackPanel-screenshot img").toHaveCount(1);
    expect(".o-usl-FeedbackPanel-screenshot input").toBeChecked();
    expect(".o-usl-FeedbackPanel").toHaveText(/visible to all internal employees/);
    expect(".o-usl-FeedbackPanel").toHaveText(/sent to Google Gemini/);
    await contains(".o-usl-FeedbackPanel-screenshot input").click();
    expect(".o-usl-FeedbackPanel-screenshot input").not.toBeChecked();
    expect.verifySteps(["start", "upload screenshot", "remove screenshot"]);
});

test("capture fallback keeps context opt-in and manual attachments usable", async () => {
    let attachmentId = 90;
    onRpc("usl.feedback.submission", "feedback_start", () => ({
        draft_id: 42,
        company_name: "Unstatic Labs",
        context_available: true,
        recent: [],
    }));
    onRpc("usl.feedback.submission", "feedback_add_attachment", ({ args }) => {
        expect(args.at(-1)).toBe(false);
        return { id: attachmentId++, name: args[1], mimetype: args[2] };
    });
    await mountWithCleanup(FeedbackPanel, {
        props: {
            close() {},
            pageContext: { action_id: 7, model: "project.task", res_id: 9 },
            screenshot: false,
            captureError: true,
        },
    });
    expect(".o-usl-FeedbackPanel").toHaveText(/Screenshot capture was skipped/);
    expect("#usl_feedback_context").not.toBeChecked();
    await contains("#usl_feedback_files").click();
    await setInputFiles(
        new File(["reproduction"], "reproduction.txt", { type: "text/plain" })
    );
    await animationFrame();
    expect(".o-usl-FeedbackPanel").toHaveText(/reproduction.txt/);
    expect("#usl_feedback_files").not.toHaveAttribute("disabled");
});
