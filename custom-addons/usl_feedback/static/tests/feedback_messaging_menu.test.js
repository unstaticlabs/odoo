import { expect, test } from "@odoo/hoot";
import { setInputFiles } from "@odoo/hoot-dom";
import { animationFrame } from "@odoo/hoot-mock";
import { Component, xml } from "@odoo/owl";
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
import { FeedbackChatWindowService } from "../src/js/feedback_chat_window";

defineMailModels();

class TestChatter extends Component {
    static template = xml`<div class="o-test-FeedbackChatter" t-att-data-composer="props.composer ? 'enabled' : 'disabled'"/>`;
    static props = ["threadModel", "threadId", "composer"];
}

function feedbackTask(values = {}) {
    return {
        id: 71,
        name: "Clarify the reload status",
        description_text: "After reload, the next action is unclear.",
        category: "ux",
        priority: "2",
        stage: "Inbox",
        agent_state: "waiting",
        agent_error: false,
        reporter_id: 4,
        is_reporter: true,
        can_manage: false,
        screenshot_attachment_id: false,
        screenshot_name: false,
        related_feedback: [],
        ...values,
    };
}

function mockFeedbackStart(recent = []) {
    onRpc("usl.feedback.submission", "feedback_start", () => ({
        draft_id: 41,
        company_name: "Unstatic Labs",
        context_available: true,
        recent,
    }));
}

async function mountFeedbackPanel(props = {}) {
    patchWithCleanup(FeedbackPanel.components, { Chatter: TestChatter });
    return mountWithCleanup(FeedbackPanel, {
        props: {
            close() {},
            pageContext: { action_id: 7, model: "project.task", res_id: 9 },
            screenshot: false,
            captureError: false,
            ...props,
        },
    });
}

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

test("feedback chat window preserves its draft while folding and clears evidence on close", () => {
    let folded = false;
    const opened = [];
    const nativeWindow = {
        fold() {
            folded = true;
            opened.pop();
        },
    };
    opened.push(nativeWindow);
    const store = {
        chatHub: {
            opened,
            maxOpened: 1,
        },
    };
    const service = new FeedbackChatWindowService(
        { bus: { trigger() {} } },
        { "mail.store": store }
    );
    const payload = {
        pageContext: { action_id: 7 },
        screenshot: { name: "screen.jpg" },
        captureError: false,
    };

    service.open(payload);
    expect(folded).toBe(true);
    expect(service.mode).toBe("open");
    expect(service.screenshot.name).toBe("screen.jpg");

    service.fold();
    service.open();
    expect(service.mode).toBe("open");
    expect(service.pageContext.action_id).toBe(7);

    service.close();
    expect(service.mode).toBe("closed");
    expect(service.pageContext).toBe(false);
    expect(service.screenshot).toBe(false);
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

test("first message creates the conversation and keeps page context opt-in", async () => {
    mockFeedbackStart();
    onRpc("usl.feedback.submission", "feedback_submit_initial", ({ args }) => {
        expect(args.slice(1)).toEqual([
            "The next action disappears after reload.",
            false,
        ]);
        expect.step("created inbox task");
        return feedbackTask({ agent_state: "queued" });
    });
    onRpc("project.task", "feedback_poll_agent", () =>
        feedbackTask({ agent_state: "waiting" })
    );
    await mountFeedbackPanel();
    await contains("#usl_feedback_message").edit(
        "The next action disappears after reload."
    );
    await contains(".o-usl-FeedbackPanel button:contains('Start conversation')").click();
    await animationFrame();
    expect(".o-test-FeedbackChatter").toHaveCount(1);
    expect(".o-test-FeedbackChatter").toHaveAttribute("data-composer", "enabled");
    expect.verifySteps(["created inbox task"]);
});

test("conversation renders processing, clarification, error, ready, and success states", async () => {
    mockFeedbackStart();
    const component = await mountFeedbackPanel();
    Object.assign(component.state, {
        phase: "conversation",
        task: feedbackTask({ agent_state: "processing" }),
    });
    await animationFrame();
    expect(".o-usl-FeedbackPanel").toHaveText(/reviewing your latest message/);
    expect(".o-test-FeedbackChatter").toHaveAttribute("data-composer", "disabled");

    component.state.task = feedbackTask({ agent_state: "waiting" });
    await animationFrame();
    expect(".o-test-FeedbackChatter").toHaveAttribute("data-composer", "enabled");

    component.state.task = feedbackTask({
        agent_state: "error",
        agent_error: "The assistant is temporarily unavailable. Your feedback is saved.",
    });
    await animationFrame();
    expect(".o-usl-FeedbackPanel .alert-warning").toHaveText(/feedback is saved/);
    expect(".o-usl-FeedbackPanel .alert-warning button").toHaveText("Retry");

    component.state.task = feedbackTask({ agent_state: "ready" });
    await animationFrame();
    expect(".o-usl-FeedbackPanel-ready").toHaveText(/Brief ready for your confirmation/);
    expect(".o-usl-FeedbackPanel-ready").toHaveText(/Clarify the reload status/);
    expect(".o-usl-FeedbackPanel-ready").toHaveText(/After reload/);
    expect(".o-usl-FeedbackPanel-ready button").toHaveCount(2);

    component.state.task = feedbackTask({ agent_state: "triaged", stage: "Triage" });
    await animationFrame();
    expect(".o-usl-FeedbackPanel .alert-success").toHaveText(/now in Triage/);
    expect(".o-test-FeedbackChatter").toHaveAttribute("data-composer", "disabled");
});

test("reporter confirmation is guarded by the ready card and updates to Triage", async () => {
    mockFeedbackStart();
    onRpc("project.task", "feedback_confirm_triage", ({ args }) => {
        expect(args[0]).toEqual([71]);
        expect.step("confirmed");
        return feedbackTask({ agent_state: "triaged", stage: "Triage" });
    });
    const component = await mountFeedbackPanel();
    Object.assign(component.state, {
        phase: "conversation",
        task: feedbackTask({ agent_state: "ready" }),
    });
    await animationFrame();
    await contains(
        ".o-usl-FeedbackPanel-ready button:contains('Confirm and send to Triage')"
    ).click();
    expect(".o-usl-FeedbackPanel .alert-success").toHaveText(/now in Triage/);
    expect.verifySteps(["confirmed"]);
});

test("draft and provider errors preserve recovery actions and reporter input", async () => {
    mockFeedbackStart();
    const component = await mountFeedbackPanel();
    await contains("#usl_feedback_message").edit("Keep this text while recovering.");
    component.showError({ message: "The network request timed out." });
    await animationFrame();
    expect(".o-usl-FeedbackPanel .alert-danger").toHaveText(/timed out/);
    expect("#usl_feedback_message").toHaveValue("Keep this text while recovering.");

    Object.assign(component.state, {
        phase: "start_error",
        error: "Feedback could not load.",
    });
    await animationFrame();
    expect(".o-usl-FeedbackPanel [role='alert']").toHaveText(/could not load/);
    expect(".o-usl-FeedbackPanel [role='alert'] button").toHaveText("Try again");
});
