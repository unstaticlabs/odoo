import "@mail/chatter/web/chatter_patch";

import { Chatter } from "@mail/chatter/web_portal_project/chatter";
import { MessagingMenu } from "@mail/core/public_web/messaging_menu";
import { browser } from "@web/core/browser/browser";
import { _t } from "@web/core/l10n/translation";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";

import { Component, onWillStart, onWillUnmount, useRef, useState } from "@odoo/owl";


const MAX_SCREENSHOT_DIMENSION = 1920;
const MAX_SCREENSHOT_BYTES = 5 * 1024 * 1024;

function positiveInteger(value) {
    const normalized = Number(value);
    return Number.isSafeInteger(normalized) && normalized > 0 ? normalized : false;
}

export function feedbackPageContext(actionController, viewport = browser.visualViewport) {
    const props = actionController?.props || {};
    const action = actionController?.action || {};
    return {
        action_id: positiveInteger(action.id || props.actionId),
        model: typeof props.resModel === "string" ? props.resModel : false,
        res_id: positiveInteger(props.resId),
        viewport_width: positiveInteger(Math.round(viewport?.width || browser.innerWidth)),
        viewport_height: positiveInteger(Math.round(viewport?.height || browser.innerHeight)),
    };
}

export async function captureFeedbackScreenshot(mediaDevices = browser.navigator.mediaDevices) {
    if (!mediaDevices?.getDisplayMedia) {
        return false;
    }
    let stream;
    try {
        stream = await mediaDevices.getDisplayMedia({ video: true, audio: false });
        const video = document.createElement("video");
        video.muted = true;
        video.playsInline = true;
        video.srcObject = stream;
        await new Promise((resolve, reject) => {
            video.onloadedmetadata = resolve;
            video.onerror = reject;
        });
        await video.play();
        const scale = Math.min(
            1,
            MAX_SCREENSHOT_DIMENSION / Math.max(video.videoWidth, video.videoHeight)
        );
        const canvas = document.createElement("canvas");
        canvas.width = Math.max(1, Math.round(video.videoWidth * scale));
        canvas.height = Math.max(1, Math.round(video.videoHeight * scale));
        const context = canvas.getContext("2d");
        let quality = 0.86;
        let dataUrl;
        for (let attempt = 0; attempt < 12; attempt++) {
            context.drawImage(video, 0, 0, canvas.width, canvas.height);
            dataUrl = canvas.toDataURL("image/jpeg", quality);
            const encoded = dataUrl.slice(dataUrl.indexOf(",") + 1);
            if (Math.ceil((encoded.length * 3) / 4) <= MAX_SCREENSHOT_BYTES) {
                return {
                    name: `odoo-feedback-${new Date().toISOString().replaceAll(":", "-")}.jpg`,
                    mimetype: "image/jpeg",
                    data: encoded,
                    previewUrl: dataUrl,
                    width: canvas.width,
                    height: canvas.height,
                };
            }
            if (quality > 0.5) {
                quality -= 0.1;
            } else {
                canvas.width = Math.max(1, Math.round(canvas.width * 0.8));
                canvas.height = Math.max(1, Math.round(canvas.height * 0.8));
                quality = 0.8;
            }
        }
        throw new Error(_t("The screenshot is too large. Continue without it or capture a smaller area."));
    } finally {
        for (const track of stream?.getTracks() || []) {
            track.stop();
        }
    }
}

function fileAsBase64(file) {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onerror = reject;
        reader.onload = () => resolve(String(reader.result).split(",", 2)[1]);
        reader.readAsDataURL(file);
    });
}

export async function focusFeedbackComposer(root) {
    let input = root.querySelector(".o-mail-Composer-input");
    if (!input) {
        const trigger = root.querySelector(".o-mail-Chatter-sendMessage");
        if (!trigger) {
            return false;
        }
        trigger.click();
        await new Promise((resolve) => browser.setTimeout(resolve, 0));
        input = root.querySelector(".o-mail-Composer-input");
    }
    input?.focus();
    return Boolean(input);
}

export class FeedbackPanel extends Component {
    static template = "usl_feedback.FeedbackPanel";
    static components = { Chatter };
    static props = ["close", "pageContext", "screenshot?", "captureError?"];

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.rootRef = useRef("root");
        this.state = useState({
            phase: "loading",
            draftId: false,
            contextAvailable: false,
            includeContext: false,
            message: "",
            screenshotSelected: Boolean(this.props.screenshot),
            screenshotAttachmentId: false,
            attachments: [],
            recent: [],
            task: false,
            error: false,
            busy: false,
        });
        this.pollTimer = false;
        onWillStart(() => this.startDraft());
        onWillUnmount(() => browser.clearTimeout(this.pollTimer));
    }

    async startDraft() {
        this.state.error = false;
        try {
            const result = await this.orm.call(
                "usl.feedback.submission",
                "feedback_start",
                [this.props.pageContext]
            );
            Object.assign(this.state, {
                phase: "draft",
                draftId: result.draft_id,
                contextAvailable: result.context_available,
                recent: result.recent,
            });
            if (this.props.screenshot && this.state.screenshotSelected) {
                await this.uploadScreenshot();
            }
        } catch (error) {
            this.state.phase = "start_error";
            this.showError(error);
        }
    }

    async retryStartDraft() {
        this.state.phase = "loading";
        await this.startDraft();
    }

    async uploadScreenshot() {
        const screenshot = this.props.screenshot;
        if (!screenshot || this.state.screenshotAttachmentId) {
            return;
        }
        const attachment = await this.orm.call(
            "usl.feedback.submission",
            "feedback_add_attachment",
            [
                [this.state.draftId],
                screenshot.name,
                screenshot.mimetype,
                screenshot.data,
                true,
            ]
        );
        this.state.screenshotAttachmentId = attachment.id;
    }

    async toggleScreenshot() {
        if (this.state.busy) {
            return;
        }
        this.state.busy = true;
        this.state.screenshotSelected = !this.state.screenshotSelected;
        try {
            if (this.state.screenshotSelected) {
                await this.uploadScreenshot();
            } else if (this.state.screenshotAttachmentId) {
                await this.orm.call(
                    "usl.feedback.submission",
                    "feedback_remove_attachment",
                    [[this.state.draftId], this.state.screenshotAttachmentId]
                );
                this.state.screenshotAttachmentId = false;
            }
        } catch (error) {
            this.state.screenshotSelected = !this.state.screenshotSelected;
            this.showError(error);
        } finally {
            this.state.busy = false;
        }
    }

    async onFilesSelected(event) {
        const screenshotCount = this.state.screenshotAttachmentId ? 1 : 0;
        const files = [...event.target.files].slice(
            0,
            10 - this.state.attachments.length - screenshotCount
        );
        this.state.busy = true;
        try {
            for (const file of files) {
                const attachment = await this.orm.call(
                    "usl.feedback.submission",
                    "feedback_add_attachment",
                    [
                        [this.state.draftId],
                        file.name,
                        file.type || "application/octet-stream",
                        await fileAsBase64(file),
                        false,
                    ]
                );
                this.state.attachments.push(attachment);
            }
        } catch (error) {
            this.showError(error);
        } finally {
            this.state.busy = false;
            event.target.value = "";
        }
    }

    async removeAttachment(attachment) {
        try {
            await this.orm.call(
                "usl.feedback.submission",
                "feedback_remove_attachment",
                [[this.state.draftId], attachment.id]
            );
            this.state.attachments = this.state.attachments.filter(
                (item) => item.id !== attachment.id
            );
        } catch (error) {
            this.showError(error);
        }
    }

    async submitInitial() {
        if (!this.state.message.trim() || this.state.busy) {
            return;
        }
        this.state.busy = true;
        this.state.error = false;
        try {
            const task = await this.orm.call(
                "usl.feedback.submission",
                "feedback_submit_initial",
                [[this.state.draftId], this.state.message, this.state.includeContext]
            );
            this.state.task = task;
            this.state.phase = "conversation";
            this.schedulePoll(0);
            if (task.context_omitted) {
                this.notification.add(
                    _t("Page details were left out because you no longer have access to this record."),
                    { type: "warning" }
                );
            }
        } catch (error) {
            this.showError(error);
        } finally {
            this.state.busy = false;
        }
    }

    async resumeTask(task) {
        this.state.task = task;
        this.state.phase = "conversation";
        this.schedulePoll(0);
    }

    async showRecent() {
        browser.clearTimeout(this.pollTimer);
        this.state.error = false;
        this.state.busy = true;
        try {
            this.state.recent = await this.orm.call("project.task", "feedback_recent", []);
            this.state.phase = "recent";
        } catch (error) {
            this.showError(error);
        } finally {
            this.state.busy = false;
        }
    }

    async newConversation() {
        browser.clearTimeout(this.pollTimer);
        this.state.phase = "loading";
        this.state.task = false;
        this.state.message = "";
        this.state.error = false;
        this.state.attachments = [];
        this.state.screenshotAttachmentId = false;
        this.state.screenshotSelected = Boolean(this.props.screenshot);
        await this.startDraft();
    }

    schedulePoll(delay = 2000) {
        browser.clearTimeout(this.pollTimer);
        this.pollTimer = browser.setTimeout(() => this.poll(), delay);
    }

    async poll() {
        if (!this.state.task) {
            return;
        }
        try {
            this.state.task = await this.orm.call(
                "project.task",
                "feedback_poll_agent",
                [[this.state.task.id]]
            );
        } catch (error) {
            this.showError(error, false);
        }
        if (["queued", "processing"].includes(this.state.task?.agent_state)) {
            this.schedulePoll();
        }
    }

    async retry() {
        this.state.busy = true;
        try {
            this.state.task = await this.orm.call(
                "project.task",
                "feedback_retry_agent",
                [[this.state.task.id]]
            );
            this.schedulePoll(0);
        } catch (error) {
            this.showError(error);
        } finally {
            this.state.busy = false;
        }
    }

    async confirmTriage() {
        this.state.busy = true;
        try {
            this.state.task = await this.orm.call(
                "project.task",
                "feedback_confirm_triage",
                [[this.state.task.id]]
            );
        } catch (error) {
            this.showError(error);
        } finally {
            this.state.busy = false;
        }
    }

    async keepRefining() {
        if (!(await focusFeedbackComposer(this.rootRef.el))) {
            this.notification.add(_t("Open the conversation to add details."), {
                type: "warning",
            });
        }
    }

    async openBoard() {
        const action = await this.orm.call("project.project", "feedback_open_board", []);
        action.name = _t("Feedback");
        action.display_name = action.name;
        this.props.close();
        await this.action.doAction(action);
    }

    showError(error, persistent = true) {
        const message =
            error?.data?.message ||
            error?.message ||
            _t("We couldn’t complete that action. Try again.");
        if (persistent) {
            this.state.error = message;
        } else {
            this.notification.add(message, { type: "warning" });
        }
    }
}

patch(MessagingMenu.prototype, {
    setup() {
        super.setup(...arguments);
        this.feedbackChatWindow = useService("usl_feedback.chat_window");
    },

    async onClickFeedback() {
        if (!this.feedbackChatWindow.isClosed) {
            this.feedbackChatWindow.open();
            this.dropdown.close();
            return;
        }
        const pageContext = feedbackPageContext(this.env.services.action.currentController);
        let screenshot = false;
        let captureError = false;
        try {
            screenshot = await captureFeedbackScreenshot();
            captureError = !screenshot;
        } catch {
            captureError = true;
        }
        this.feedbackChatWindow.open({ pageContext, screenshot, captureError });
        this.dropdown.close();
    },
});
