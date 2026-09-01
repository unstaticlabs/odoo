import { expect, test } from "@odoo/hoot";
import { setInputFiles, waitFor } from "@odoo/hoot-dom";
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
    FeedbackPanel,
    feedbackPageContext,
    focusFeedbackComposer,
} from "../src/js/feedback_messaging_menu";
import {
    feedbackChatWindowService,
    FeedbackChatWindowService,
} from "../src/js/feedback_chat_window";
import {
    captureFeedbackPagePreview,
    isFeedbackPreviewNodeAllowed,
    MAX_PAGE_PREVIEW_BYTES,
} from "../src/js/feedback_page_preview";
import {
    cloneScrollPosition,
    isSafeCaptureResourceUrl,
} from "../src/lib/html_to_image";

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
        category: "UX",
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
            captureState: "idle",
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

test("page preview keeps Odoo and excludes messaging, alerts, and private fields", () => {
    for (const selector of [
        ".o-mail-ChatHub",
        ".o-mail-MessagingMenu",
        ".o_notification_manager",
        ".o-usl-FeedbackButton",
        "[data-usl-feedback-private]",
        "input[type='password']",
    ]) {
        const node = document.createElement(selector.startsWith("input") ? "input" : "div");
        if (selector.startsWith(".")) {
            node.className = selector.slice(1);
        } else if (selector === "[data-usl-feedback-private]") {
            node.dataset.uslFeedbackPrivate = "";
        } else {
            node.type = "password";
        }
        expect(isFeedbackPreviewNodeAllowed(node)).toBe(false);
    }
    expect(isFeedbackPreviewNodeAllowed(document.createElement("main"))).toBe(true);
});

test("page preview rejects external resources and preserves visible scroll offsets", () => {
    expect(isSafeCaptureResourceUrl("/web/image/1")).toBe(true);
    expect(isSafeCaptureResourceUrl("data:image/png;base64,AA==")).toBe(true);
    expect(isSafeCaptureResourceUrl("https://example.com/private.png")).toBe(false);

    const source = document.createElement("div");
    const clone = document.createElement("div");
    clone.appendChild(document.createElement("section"));
    Object.defineProperties(source, {
        scrollLeft: { value: 12 },
        scrollTop: { value: 48 },
    });
    cloneScrollPosition(source, clone);
    expect(clone.style.overflow).toBe("hidden");
    expect(clone.firstElementChild.style.transform).toBe("translate(-12px, -48px)");
});

test("page preview renders the Odoo viewport, compresses locally, and releases once", async () => {
    patchWithCleanup(browser, { innerHeight: 1440, innerWidth: 2560 });
    const root = document.createElement("main");
    root.className = "o_web_client";
    const qualities = [];
    const canvas = {
        height: 1080,
        width: 1920,
        toBlob(callback, mimetype, quality) {
            qualities.push(quality);
            callback({ size: quality > 0.5 ? MAX_PAGE_PREVIEW_BYTES + 1 : 1024, type: mimetype });
        },
    };
    const revoked = [];
    const preview = await captureFeedbackPagePreview({
        root,
        render: async (target, options) => {
            expect(target).toBe(root);
            expect(options.canvasWidth).toBe(1920);
            expect(options.canvasHeight).toBe(1080);
            expect(options.includeQueryParams).toBe(false);
            return canvas;
        },
        urlApi: {
            createObjectURL: () => "blob:feedback-preview",
            revokeObjectURL: (url) => revoked.push(url),
        },
        now: () => new Date("2026-09-01T10:11:12Z"),
    });
    expect(preview.previewUrl).toBe("blob:feedback-preview");
    expect(preview.name).toBe("odoo-feedback-2026-09-01T10-11-12.000Z.jpg");
    expect(preview.width).toBe(1920);
    expect(preview.height).toBe(1080);
    expect(qualities.length).toBeGreaterThan(1);
    preview.release();
    preview.release();
    expect(revoked).toEqual(["blob:feedback-preview"]);
});

test("feedback chat window preserves folds and releases cancelled, late, or closed previews", () => {
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
    const captureId = service.beginCapture({ action_id: 7 });
    expect(folded).toBe(true);
    expect(service.mode).toBe("open");
    expect(service.captureState).toBe("preparing");

    let released = 0;
    service.completeCapture(captureId, {
        name: "screen.jpg",
        release: () => released++,
    });
    expect(service.captureState).toBe("ready");
    expect(service.screenshot.name).toBe("screen.jpg");

    service.cancelCapture();
    expect(service.captureState).toBe("idle");
    expect(service.screenshot).toBe(false);
    expect(released).toBe(1);
    service.completeCapture(captureId, { release: () => released++ });
    expect(released).toBe(2);

    const nextCaptureId = service.beginCapture({ action_id: 7 });
    service.completeCapture(nextCaptureId, {
        name: "screen.jpg",
        release: () => released++,
    });
    service.fold();
    service.open();
    expect(service.mode).toBe("open");
    expect(service.pageContext.action_id).toBe(7);

    service.close();
    expect(service.mode).toBe("closed");
    expect(service.pageContext).toBe(false);
    expect(service.screenshot).toBe(false);
    expect(released).toBe(3);

    service.completeCapture(nextCaptureId, { release: () => released++ });
    expect(released).toBe(4);
});

test("browser back cancels the local page preview and any late result", () => {
    const service = feedbackChatWindowService.start(
        { bus: { trigger() {} } },
        { "mail.store": { chatHub: { opened: [], maxOpened: 1 } } }
    );
    const captureId = service.beginCapture({ action_id: 7 });
    let released = 0;
    service.completeCapture(captureId, { release: () => released++ });
    window.dispatchEvent(new PopStateEvent("popstate"));
    expect(service.screenshot).toBe(false);
    expect(service.captureState).toBe("idle");
    expect(released).toBe(1);

    service.completeCapture(captureId, { release: () => released++ });
    expect(released).toBe(2);
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

test("draft keeps its default-selected page preview local until send", async () => {
    onRpc("usl.feedback.submission", "feedback_start", () => {
        expect.step("start");
        return {
            draft_id: 41,
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
    onRpc("usl.feedback.submission", "feedback_submit_initial", () => {
        expect.step("submit");
        return feedbackTask();
    });
    onRpc("project.task", "feedback_poll_agent", () => feedbackTask());
    await mountFeedbackPanel({
        clearScreenshot: () => expect.step("clear local preview"),
        screenshot: {
            name: "screen.jpg",
            mimetype: "image/jpeg",
            blob: new Blob(["screenshot"], { type: "image/jpeg" }),
            previewUrl: "blob:feedback-preview",
            width: 1440,
            height: 900,
        },
        captureState: "ready",
    });
    expect(".o-usl-FeedbackPanel-screenshot img").toHaveCount(1);
    expect(".o-usl-FeedbackPanel-screenshot input").toBeChecked();
    expect(".o-usl-FeedbackPanel").toHaveText(/All internal employees can read/);
    expect(".o-usl-FeedbackPanel").toHaveText(/Gemini receives your message/);
    expect.verifySteps(["start"]);
    await contains("#usl_feedback_message").edit("The page preview shows the issue.");
    await contains(".o-usl-FeedbackPanel button:contains('Send feedback')").click();
    await animationFrame();
    expect.verifySteps(["upload screenshot", "submit", "clear local preview"]);
});

test("deselecting a local page preview releases it without uploading", async () => {
    mockFeedbackStart();
    const component = await mountFeedbackPanel({
        clearScreenshot: () => expect.step("release local preview"),
        screenshot: {
            name: "screen.jpg",
            mimetype: "image/jpeg",
            blob: new Blob(["screenshot"], { type: "image/jpeg" }),
            previewUrl: "blob:feedback-preview",
            width: 1440,
            height: 900,
        },
        captureState: "ready",
    });
    await component.toggleScreenshot();
    await animationFrame();
    expect(".o-usl-FeedbackPanel-screenshot input").not.toBeChecked();
    expect.verifySteps(["release local preview"]);
});

test("capture fallback keeps context opt-in and manual attachments usable", async () => {
    let attachmentId = 90;
    onRpc("usl.feedback.submission", "feedback_start", () => ({
        draft_id: 42,
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
            captureState: "error",
        },
    });
    expect(".o-usl-FeedbackPanel").toHaveText(/Page preview unavailable/);
    expect("#usl_feedback_context").not.toBeChecked();
    await contains("#usl_feedback_files").click();
    await setInputFiles(
        new File(["reproduction"], "reproduction.txt", { type: "text/plain" })
    );
    await waitFor(".o-usl-FeedbackPanel .badge:contains('reproduction.txt')");
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
    await contains(".o-usl-FeedbackPanel button:contains('Send feedback')").click();
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
    expect(".o-usl-FeedbackPanel").toHaveText(/Reviewing your feedback/);
    expect(".o-test-FeedbackChatter").toHaveAttribute("data-composer", "disabled");

    component.state.task = feedbackTask({ agent_state: "waiting" });
    await animationFrame();
    expect(".o-test-FeedbackChatter").toHaveAttribute("data-composer", "enabled");

    component.state.task = feedbackTask({
        agent_state: "error",
        agent_error: "The assistant couldn’t reply. Your feedback is saved.",
    });
    await animationFrame();
    expect(".o-usl-FeedbackPanel .alert-warning").toHaveText(/feedback is saved/);
    expect(".o-usl-FeedbackPanel .alert-warning button").toHaveText("Try again");

    component.state.task = feedbackTask({ agent_state: "ready" });
    await animationFrame();
    expect(".o-usl-FeedbackPanel-ready").toHaveText(/Review your feedback/);
    expect(".o-usl-FeedbackPanel-ready").toHaveText(/Clarify the reload status/);
    expect(".o-usl-FeedbackPanel-ready").toHaveText(/After reload/);
    expect(".o-usl-FeedbackPanel-ready button").toHaveCount(2);

    component.state.task = feedbackTask({ agent_state: "triaged", stage: "Triage" });
    await animationFrame();
    expect(".o-usl-FeedbackPanel .alert-success").toHaveText(/Sent to the product team/);
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
        ".o-usl-FeedbackPanel-ready button:contains('Send to product team')"
    ).click();
    expect(".o-usl-FeedbackPanel .alert-success").toHaveText(/Sent to the product team/);
    expect.verifySteps(["confirmed"]);
});

test("opening the board keeps the floating feedback conversation open", async () => {
    mockFeedbackStart();
    onRpc("project.project", "feedback_open_board", () => ({
        type: "ir.actions.act_window",
        name: "Product Feedback",
        res_model: "project.task",
        views: [[false, "kanban"]],
        domain: [],
        context: {},
        help: "<p class='o_view_nocontent_smiling_face'>No feedback yet</p>",
    }));
    const component = await mountFeedbackPanel({
        close: () => expect.step("closed"),
    });
    component.action = {
        async doAction(action) {
            expect(String(action.help)).toMatch(/<p class='o_view_nocontent_smiling_face'>/);
            expect.step("board opened");
        },
    };

    await component.openBoard();

    expect.verifySteps(["board opened"]);
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
