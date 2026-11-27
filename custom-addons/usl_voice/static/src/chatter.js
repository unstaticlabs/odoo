import { markRaw, onMounted, onWillUnmount, useEffect } from "@odoo/owl";
import { useExternalListener, useRef, useState } from "@web/owl2/utils";
import { _t } from "@web/core/l10n/translation";
import { user } from "@web/core/user";
import { useService } from "@web/core/utils/hooks";
import { patch } from "@web/core/utils/patch";
import { Composer } from "@mail/core/common/composer";
import { registerComposerAction } from "@mail/core/common/composer_actions";
import { VoiceCapture } from "./capture";
import { VoiceMeter } from "./meter";
import {
    claimRecording, disownRecording, downloadRecording, forgetRecording, keepRecording, keptRecordings, newRecordingId,
    offerUndo, ownRecording,
} from "./recording_store";
import { uploadVoiceNote } from "./voice_note_upload";

/**
 * A voice note is the writer's words: it is kept in this browser from the moment recording stops
 * until the server holds it, and it is uploaded to the record it was recorded for, whatever
 * screen is open by then. When the upload fails it stays, with Retry and Download.
 */
patch(Composer.prototype, {
    setup() {
        super.setup();
        this.uslVoice = useState({ mode: "idle" });
        this.uslVoiceNotify = useService("notification");
        this.uslVoiceBar = useRef("usl-voice-bar");
        useEffect(() => this.watchUslPending(), () => [this.props.composer.attachments.length]);
        // Leaving with a voice note that exists nowhere but in memory asks first.
        useExternalListener(window, "beforeunload", (ev) => {
            if (this.uslUnsafe()) {
                ev.preventDefault();
                ev.returnValue = "";
            }
        });
        onMounted(() => this.adoptUslPending());
        onWillUnmount(() => this.suspendUslVoice());
    },
    /** A recording under way, or a finished one the browser could not store. */
    uslUnsafe() {
        return Boolean(this.uslCapture || (this.uslKept && !this.uslKept.persisted) ||
            (this.uslPending && !this.uslPending.kept.persisted));
    },
    /**
     * A voice note uploaded as a pending attachment of this composer, whose composer was closed
     * before Send: when the composer opens again with that attachment, it takes the note back.
     */
    async adoptUslPending() {
        const attachments = this.props.composer.attachments;
        if (!attachments.length || !this.thread) {
            return;
        }
        const ids = new Set(attachments.map((attachment) => attachment.id));
        for (const entry of await keptRecordings(user.userId)) {
            const there = entry.destination?.model === this.thread.model && entry.destination?.id === this.thread.id;
            if (entry.kind === "voice_note" && there && ids.has(entry.attachmentId) && await claimRecording(entry.id)) {
                if (this.uslGone || this.uslPending) {
                    disownRecording(entry.id);
                } else {
                    const kept = { id: entry.id, file: entry.blob, at: entry.createdAt, destination: entry.destination, persisted: true, entry };
                    this.uslPending = { kept, attachmentId: entry.attachmentId, seen: this.uslAttached(entry.attachmentId) };
                }
                return;
            }
        }
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
        return (this.uslVoice && ["starting", "recording", "uploading"].includes(this.uslVoice.mode)) || super.isSendButtonDisabled;
    },
    onKeydown(ev) {
        if (ev.key === "Enter" && ["starting", "recording", "uploading"].includes(this.uslVoice.mode)) {
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
    /** Escape or the bin while recording: what was said is kept for ten seconds, with Undo. */
    cancelUslVoice() {
        const capture = this.uslCapture;
        this.uslCapture = null;
        const file = this.uslVoice.mode === "recording" && capture?.finish();
        capture?.discard();
        this.uslVoice.mode = "idle";
        if (file) {
            const kept = this.holdUslVoice(file);
            this.discardUslVoice(kept);
        }
    },
    /** Keep the finished recording in this browser, with the record it was made for. */
    holdUslVoice(file) {
        const kept = {
            id: newRecordingId(),
            file,
            at: Date.now(),
            destination: this.uslVoiceDestination,
            persisted: false,
        };
        kept.entry = {
            id: kept.id, kind: "voice_note", blob: file, createdAt: kept.at, userId: user.userId,
            destination: kept.destination,
        };
        ownRecording(kept.id);
        keepRecording(kept.entry).then((ok) => (kept.persisted = ok));
        return kept;
    },
    /** Let the recording go, with ten seconds to change one's mind. */
    discardUslVoice(kept = this.uslKept) {
        if (!kept) {
            return;
        }
        if (this.uslKept === kept) {
            this.uslKept = null;
            this.uslVoice.mode = "idle";
        }
        offerUndo(this.uslVoiceNotify, kept.at, {
            undo: () => this.undoUslVoice(kept),
            expire: () => forgetRecording(kept.id),
        });
    },
    /**
     * Undo goes on where the writer left off only if the composer is free: when another recording
     * is under way or waiting in it, the note is offered as an unused one instead.
     */
    undoUslVoice(kept) {
        const free = !this.uslGone && this.uslVoice.mode === "idle" && !this.uslCapture && !this.uslKept && !this.uslPending;
        if (free) {
            this.uploadUslVoice(kept);
        } else {
            disownRecording(kept.id);
        }
    },
    async startUslVoice() {
        if (this.uslVoice.mode !== "idle") {
            return;
        }
        this.uslVoice.mode = "starting";
        this.focusUslVoiceBar();
        const capture = markRaw(new VoiceCapture());
        this.uslCapture = capture;
        this.uslVoiceDestination = { model: this.thread.model, id: this.thread.id, name: this.thread.name || this.thread.display_name || "" };
        try {
            await capture.start(() => this.stopUslVoice());
            if (this.uslCapture === capture) {
                this.uslVoice.mode = "recording";
                this.focusUslVoiceBar();
            }
        } catch {
            if (this.uslCapture === capture) {
                this.uslCapture?.discard();
                this.uslCapture = null;
                this.uslVoice.mode = "idle";
                this.uslVoiceNotify.add(_t("Check microphone access and try again."), { type: "warning" });
            }
        }
    },
    async stopUslVoice() {
        if (this.uslVoice.mode !== "recording") {
            return;
        }
        const file = this.uslCapture.finish();
        this.uslCapture = null;
        if (!file) {
            this.uslVoice.mode = "idle";
            return;
        }
        await this.uploadUslVoice(this.holdUslVoice(file));
    },
    /**
     * Upload the recording to the record it was made for. On the same record it becomes the
     * voice note of this message, as before; when the writer has moved on, it is attached to its
     * record instead, so that nothing is dropped. A failure keeps it, with Retry and Download.
     */
    async uploadUslVoice(kept) {
        const here = !this.uslGone && this.thread?.model === kept.destination.model && this.thread?.id === kept.destination.id;
        if (!this.uslGone) {
            this.uslKept = kept;
            this.uslVoice.mode = "uploading";
        }
        let saved = false;
        let attachment;
        try {
            if (here) {
                attachment = await this.attachmentUploader.uploadFile(kept.file, { voice: true, thread: this.thread });
                saved = Boolean(attachment);
            } else {
                saved = await uploadVoiceNote(this.env, kept.destination, kept.file);
            }
        } catch {
            saved = false;
        }
        if (saved && here) {
            // The server holds a pending attachment of this composer, which Odoo deletes a day later
            // if no message carries it: the browser keeps its copy until the message is posted or
            // the writer removes the attachment. A composer that is gone leaves it for the next page.
            if (this.uslGone) {
                disownRecording(kept.id);
            } else {
                this.uslPending = { kept, attachmentId: attachment.id, seen: this.uslAttached(attachment.id) };
                // The pending attachment is only the composer's: remember which one, so that a
                // composer opened again for this record takes the note back.
                kept.saving = keepRecording({ ...kept.entry, attachmentId: attachment.id }).then((ok) => (kept.persisted = ok));
            }
        } else if (saved) {
            await forgetRecording(kept.id);
            this.uslVoiceNotify.add(_t("Voice note attached to %s.", kept.destination.name || _t("the record")), { type: "success" });
        } else if (this.uslGone) {
            disownRecording(kept.id); // offered again at the start of the next page
            this.uslVoiceNotify.add(_t("The voice note could not be uploaded. Your recording is kept."), { type: "warning" });
        }
        if (!this.uslGone) {
            if (saved) {
                this.uslKept = null;
            }
            this.uslVoice.mode = saved ? "idle" : "failed";
        }
    },
    uslAttached(id) {
        return this.props.composer.attachments.some((attachment) => attachment.id === id);
    },
    /** The pending voice note left the composer: posted with the message, or removed on purpose. */
    watchUslPending() {
        const pending = this.uslPending;
        if (!pending) {
            return;
        }
        if (this.uslAttached(pending.attachmentId)) {
            pending.seen = true;
        } else if (pending.seen) {
            this.uslPending = null;
            forgetRecording(pending.kept.id);
        }
    },
    retryUslVoice() {
        if (this.uslKept && this.uslVoice.mode === "failed") {
            return this.uploadUslVoice(this.uslKept);
        }
    },
    downloadUslVoice() {
        if (this.uslKept) {
            downloadRecording(this.uslKept.file, this.uslKept.at);
        }
    },
    /** The composer goes away (the form is closed, the writer navigates): stop and keep, never drop. */
    suspendUslVoice() {
        this.uslGone = true;
        const capture = this.uslCapture;
        this.uslCapture = null;
        if (capture) {
            const file = this.uslVoice.mode === "recording" && capture.finish();
            capture.discard();
            if (file) {
                this.uploadUslVoice(this.holdUslVoice(file));
            }
        } else if (this.uslKept && this.uslVoice.mode === "failed") {
            disownRecording(this.uslKept.id);
        }
        const pending = this.uslPending;
        if (pending) {
            // Still waiting in the composer: not sent, so it is offered again. Already out: it was sent.
            this.uslPending = null;
            if (this.uslAttached(pending.attachmentId)) {
                disownRecording(pending.kept.id);
            } else {
                forgetRecording(pending.kept.id);
            }
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
