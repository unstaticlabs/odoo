import { markRaw, onWillUnmount } from "@odoo/owl";
import { useRef, useState } from "@web/owl2/utils";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";
import { patch } from "@web/core/utils/patch";
import { Composer } from "@mail/core/common/composer";
import { registerComposerAction } from "@mail/core/common/composer_actions";
import { VoiceCapture } from "./capture";
import { VoiceMeter } from "./meter";

patch(Composer.prototype, {
    setup() {
        super.setup();
        this.uslVoice = useState({ mode: "idle" });
        this.uslVoiceNotify = useService("notification");
        this.uslVoiceBar = useRef("usl-voice-bar");
        onWillUnmount(() => this.cancelUslVoice());
    },
    /** The record button disappears on click, so Escape is handled where focus goes next. */
    onUslVoiceBarKeydown(ev) {
        if (ev.key === "Escape" && ["starting", "recording"].includes(this.uslVoice.mode)) {
            ev.preventDefault();
            ev.stopPropagation();
            this.cancelUslVoice();
        }
    },
    focusUslVoiceBar() {
        // Two frames: the bar is drawn on the first one.
        requestAnimationFrame(() => requestAnimationFrame(() => this.uslVoiceBar.el?.querySelector("button")?.focus()));
    },
    get isSendButtonDisabled() {
        return (this.uslVoice && this.uslVoice.mode !== "idle") || super.isSendButtonDisabled;
    },
    onKeydown(ev) {
        if (ev.key === "Enter" && this.uslVoice.mode !== "idle") {
            ev.preventDefault();
            return;
        }
        if (ev.key === "Escape" && ["starting", "recording"].includes(this.uslVoice.mode)) {
            ev.preventDefault();
            ev.stopPropagation();
            this.cancelUslVoice();
            return;
        }
        return super.onKeydown(ev);
    },
    cancelUslVoice() {
        this.uslCapture?.discard();
        this.uslCapture = null;
        this.uslVoice.mode = "idle";
    },
    async startUslVoice() {
        if (this.uslVoice.mode !== "idle") {
            return;
        }
        this.uslVoice.mode = "starting";
        this.focusUslVoiceBar();
        const capture = markRaw(new VoiceCapture());
        this.uslCapture = capture;
        this.uslVoiceDestination = { model: this.thread.model, id: this.thread.id };
        try {
            await capture.start(() => this.stopUslVoice());
            if (this.uslCapture === capture) {
                this.uslVoice.mode = "recording";
                this.focusUslVoiceBar();
            }
        } catch {
            if (this.uslCapture === capture) {
                this.cancelUslVoice();
                this.uslVoiceNotify.add(_t("Check microphone access and try again."), { type: "warning" });
            }
        }
    },
    async stopUslVoice() {
        if (this.uslVoice.mode !== "recording") {
            return;
        }
        const sameThread = this.thread?.model === this.uslVoiceDestination.model && this.thread?.id === this.uslVoiceDestination.id;
        const file = this.uslCapture.finish();
        this.uslCapture = null;
        this.uslVoice.mode = "uploading";
        try {
            if (sameThread && file) {
                await this.attachmentUploader.uploadFile(file, { voice: true, thread: this.thread });
            }
        } finally {
            this.uslVoice.mode = "idle";
        }
    },
});

function chatter({ composer, owner }) {
    return Boolean(composer.targetThread?.id && !composer.targetThread.channel &&
        !composer.message && owner.allowUpload && navigator.mediaDevices?.getUserMedia);
}
registerComposerAction("usl-voice-record", {
    condition: (p) => chatter(p) && p.owner.uslVoice.mode === "idle" && !p.composer.voiceAttachment,
    icon: "o-usl-icon-voice-note", name: _t("Record a voice note"),
    onSelected: ({ owner }) => owner.startUslVoice(), sequenceQuick: 15,
});
patch(Composer, { components: { ...Composer.components, VoiceMeter } });
