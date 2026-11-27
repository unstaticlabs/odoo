import { Component, markRaw, onMounted, onWillUnmount } from "@odoo/owl";
import { useExternalListener, useRef, useState } from "@web/owl2/utils";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { user } from "@web/core/user";
import { useService } from "@web/core/utils/hooks";
import { VoiceCapture } from "./capture";
import { captionBox, captureTarget, caretRect, dictationTarget, typeHost, wantsMicrophone } from "./dictation_target";
import { requestDictation } from "./dictation_request";
import { VoiceMeter } from "./meter";
import {
    disownRecording, downloadRecording, forgetRecording, keepRecording, keptBus, keptRecordings, newRecordingId,
    offerUndo, ownRecording, recordingTime,
} from "./recording_store";
import { uploadVoiceNote } from "./voice_note_upload";

/**
 * Odoo closes a quick-create field, a list row or a popover on any click it sees outside
 * it, and each component decides that in its own way. The controls act on the field that
 * keeps the focus, so their pointer events are handled here, first, and reach nobody else.
 * This module is loaded before any component is mounted, so these listeners run first.
 */
const ACTIONS = ["record", "stop", "cancel", "stopWaiting", "retry", "download", "discard", "copyText"];
let mounted = null;
for (const type of ["pointerdown", "mousedown", "touchstart", "pointerup", "mouseup", "touchend", "click"]) {
    window.addEventListener(type, (ev) => {
        if (!mounted || !(ev.target instanceof Element) || !ev.target.closest(".o-usl-dictation")) {
            return;
        }
        ev.stopImmediatePropagation();
        if (type === "mousedown" && ev.target.closest(".o-usl-dictation-controls")) {
            ev.preventDefault(); // the field keeps the focus and its selection
        }
        const action = type === "click" && ev.target.closest("[data-usl-action]")?.dataset.uslAction;
        if (type === "click" && mounted.target?.isConnected &&
                document.activeElement?.closest(".o-usl-dictation-controls")) {
            mounted.target.focus(); // a touch moves the focus to the button: give it back
        }
        if (ACTIONS.includes(action)) {
            // An unused recording offered at the start of a page is named by its id.
            mounted[action](ev.target.closest("[data-usl-action]").dataset.uslId);
        }
    }, true);
}

export class FieldDictation extends Component {
    static template = "usl_voice.FieldDictation";
    static props = {};
    static components = { VoiceMeter };
    setup() {
        this.notify = useService("notification");
        mounted = this;
        this.root = useRef("controls");
        this.caption = useRef("caption");
        this.state = useState({ mode: "idle", visible: false, typing: false, above: true, x: 0, y: 0, preview: "", caption: "", clipped: false, recovered: "",
            // Why the recording waits: the worker is busy ("busy") or heard nothing ("silent").
            reason: "busy", stored: [] });
        this.refreshStored = () => this.loadStored();
        keptBus.addEventListener("change", this.refreshStored);
        useExternalListener(document, "focusin", (ev) => {
            if (ev.target.closest?.(".o-usl-dictation")) {
                return;
            }
            const next = dictationTarget(ev.target);
            if (next !== this.target && this.state.mode !== "idle") {
                this.destination = null;
            }
            this.setTarget(next);
        });
        useExternalListener(document, "input", (ev) => {
            if (this.state.mode === "idle" && !this.target?.isConnected) {
                this.target = dictationTarget(ev.target);
            }
            if (!this.target?.contains(ev.target)) {
                return;
            }
            if (this.state.mode !== "idle") {
                this.destination = null;
                this.position();
                return;
            }
            // Step aside while the writer types, and come back at the insertion point after a pause.
            this.state.typing = true;
            clearTimeout(this.typingTimer);
            this.typingTimer = setTimeout(() => {
                this.state.typing = false;
                this.position();
            }, 900);
        });
        useExternalListener(document, "selectionchange", () => this.position());
        useExternalListener(document, "focusout", (event) => {
            if (!event.relatedTarget && this.state.mode === "idle") {
                this.setTarget(null);
            }
        });
        // Escape leaves a dictation being recorded, not the form or dialog behind it. A finished
        // recording is never touched by it: Escape then belongs to whatever is open.
        useExternalListener(document, "keydown", (ev) => {
            if (ev.key === "Escape" && ["starting", "recording"].includes(this.state.mode)) {
                ev.preventDefault();
                ev.stopPropagation();
                this.cancel();
            } else if (ev.altKey && ev.shiftKey && ev.code === "KeyD" && !ev.repeat) {
                // Hands stay on the keyboard: the same keys start a dictation and end it.
                if (this.state.mode === "recording") {
                    ev.preventDefault();
                    this.stop();
                } else if (this.state.mode === "idle" && this.target) {
                    ev.preventDefault();
                    this.record();
                }
            }
        }, true);
        useExternalListener(window, "resize", () => this.position());
        useExternalListener(document, "scroll", () => this.position(), true);
        // Closing the page keeps what was said: a finished recording is already in this browser,
        // one still being recorded is stopped and kept now.
        useExternalListener(window, "pagehide", () => this.suspend());
        // Leaving with audio that is not safely kept asks first.
        useExternalListener(window, "beforeunload", (ev) => {
            if (this.unsafe()) {
                ev.preventDefault();
                ev.returnValue = "";
            }
        });
        onMounted(() => this.loadStored());
        onWillUnmount(() => {
            mounted = mounted === this ? null : mounted;
            clearTimeout(this.typingTimer);
            keptBus.removeEventListener("change", this.refreshStored);
            const id = this.recordingId;
            this.suspend();
            if (id) {
                disownRecording(id); // nobody handles it any more: the next page offers it
            }
        });
    }
    /** Audio a reload would lose: a recording under way, or a finished one that is not in the browser's storage. */
    unsafe() {
        return Boolean(this.capture || (this.pendingAudio && !this.persisted));
    }
    /** Stop and keep whatever is being recorded, and stop waiting for the worker. */
    suspend() {
        const capture = this.capture;
        if (capture) {
            this.capture = null;
            this.listening?.abort();
            const file = capture.finish();
            if (file) {
                keepRecording(this.entry(newRecordingId(), file, Date.now()));
            }
            this.reset();
        }
        this.request?.abort();
    }
    entry(id, blob, createdAt) {
        return { id, kind: "dictation", blob, createdAt, userId: user.userId };
    }
    /** The recordings kept in this browser that no screen is handling: the ones to offer again. */
    async loadStored() {
        this.state.stored = (await keptRecordings(user.userId)).map(markRaw);
    }
    setTarget(element) {
        this.target = element;
        this.state.typing = false;
        this.position();
    }
    /**
     * Sit just above the insertion point, where the eyes already are, without taking any
     * room from the field: nothing in the form moves when the controls appear or grow.
     */
    position() {
        if (this.state.mode === "idle" && !this.target?.isConnected) {
            // Odoo may redraw a field while it keeps the focus: follow the element that has it now.
            const active = document.activeElement;
            this.target = active?.closest?.(".o-usl-dictation") ? null : dictationTarget(active);
        }
        const target = this.target?.isConnected ? this.target : null;
        const caret = target ? caretRect(target) : this.anchor;
        const box = target?.getBoundingClientRect();
        const shown = Boolean(caret && box && box.width && box.height && caret.top < innerHeight &&
            caret.top + caret.height > 0 && navigator.mediaDevices?.getUserMedia);
        if (shown) {
            this.anchor = caret;
            const size = this.root.el?.offsetHeight || 28;
            const width = this.root.el?.offsetWidth || size;
            // Grow upwards from the insertion point; go below it only when there is no room above.
            this.state.above = caret.top - size - 4 >= 4;
            this.state.x = Math.max(4, Math.min(innerWidth - width - 4, caret.left + 6));
            this.state.y = this.state.above ? caret.top - 4 : Math.min(innerHeight - size - 4, caret.top + caret.height + 4);
        }
        if (this.state.mode !== "idle") {
            // The words heard so far are written in the field itself, where they will be inserted:
            // not in another field the writer moved to, nor once this one has scrolled away.
            // They stay where the text will go, wherever the writer then puts the insertion point.
            // A field that changed width has wrapped its text again, so the saved place is gone.
            // An unused recording retried at the start of a page has no field and no pinned place.
            const pin = this.pin;
            const line = pin && { height: pin.height, left: box?.left + pin.x - (target?.scrollLeft - pin.scrollLeft),
                top: box?.top + pin.y - (target?.scrollTop - pin.scrollTop) };
            const at = pin && shown && this.destination && target === this.origin && Math.abs(box.width - pin.width) < 1 && line.top + line.height / 2 > box.top &&
                line.top + line.height / 2 < box.bottom && line.left >= box.left && line.left <= box.right && captionBox(target, line, pin.host);
            this.state.caption = at ? `left:${at.left}px;top:${at.top}px;max-width:${at.width}px;height:${at.height}px;` +
                `font:${at.font};color:${at.color};background-color:${at.background}` : "";
        }
        const words = this.caption.el;
        this.state.clipped = Boolean(words && words.firstElementChild.offsetWidth > words.clientWidth);
        this.state.visible = shown;
        this.state.wanted = Boolean(target && wantsMicrophone(target));
    }
    setMode(mode) {
        this.state.mode = mode;
        this.settle();
    }
    /** Place everything now, and again once redrawn: the controls and the words change width. */
    settle() {
        this.position();
        requestAnimationFrame(() => requestAnimationFrame(() => this.position()));
    }
    /**
     * Escape or the cross while recording. What was said is kept and offered back for ten seconds:
     * pressing the wrong key must not cost the writer their words.
     */
    cancel() {
        const capture = this.capture;
        if (this.state.mode === "recording" && capture) {
            this.listening?.abort();
            this.capture = null;
            const file = capture.finish();
            if (file) {
                this.hold(file);
                this.discard();
                return;
            }
        }
        this.reset();
    }
    /** Back to rest. It never deletes kept audio: that is `discard`, which can be undone. */
    reset() {
        this.pendingAudio = null;
        this.recordingId = null;
        this.capture?.discard();
        this.capture = null;
        this.request?.abort();
        this.request = null;
        this.destination = null;
        this.listening?.abort();
        this.state.preview = "";
        this.state.reason = "busy";
        this.state.recovered = "";
        this.setMode("idle");
    }
    /** The recording is finished: keep it in this browser before anything is asked of the server. */
    async hold(file) {
        const id = (this.recordingId = newRecordingId());
        this.pendingAudio = file;
        this.recordedAt = Date.now();
        this.persisted = false;
        ownRecording(id);
        const kept = keepRecording(this.entry(id, file, this.recordedAt)).then((ok) => {
            if (this.recordingId === id) {
                this.persisted = ok;
            }
        });
        // A slow disk never makes the writer wait for the transcription.
        await Promise.race([kept, new Promise((resolve) => setTimeout(resolve, 1000))]);
    }
    /** The text is where it belongs: the audio is no longer needed. */
    release() {
        const id = this.recordingId;
        this.pendingAudio = null;
        this.recordingId = null;
        if (id) {
            forgetRecording(id);
        }
    }
    /** The audio itself, whatever state the server or the speech worker is in. */
    download(id) {
        const entry = id && this.state.stored.find((stored) => stored.id === id);
        if (entry) {
            downloadRecording(entry.blob, entry.createdAt);
        } else if (this.pendingAudio) {
            downloadRecording(this.pendingAudio, this.recordedAt);
        }
    }
    /** Discard the recording (or its transcript), with ten seconds to change one's mind. */
    discard(id) {
        const entry = id && this.state.stored.find((stored) => stored.id === id);
        if (id && !entry) {
            return;
        }
        const kept = entry
            ? { id: entry.id, at: entry.createdAt }
            : { id: this.recordingId, at: this.recordedAt, file: this.pendingAudio, recovered: this.state.recovered,
                reason: this.state.reason, destination: this.destination };
        if (!kept.id) {
            this.reset();
            return;
        }
        ownRecording(kept.id);
        if (entry) {
            this.state.stored = this.state.stored.filter((stored) => stored !== entry);
        } else {
            this.reset();
        }
        offerUndo(this.notify, kept.at, {
            undo: () => this.undiscard(kept),
            expire: () => forgetRecording(kept.id),
        });
    }
    undiscard(kept) {
        if (kept.file && this.state.mode === "idle" && !this.pendingAudio && !this.state.recovered) {
            this.recordingId = kept.id;
            this.pendingAudio = kept.file;
            this.recordedAt = kept.at;
            this.persisted = true;
            this.destination = kept.destination;
            this.state.reason = kept.reason;
            this.state.recovered = kept.recovered;
            this.setMode(kept.recovered ? "idle" : "retry");
        } else {
            // Something else is under way: the recording waits with the other unused ones.
            disownRecording(kept.id);
        }
    }
    async record() {
        if (this.state.mode !== "idle" || this.state.recovered || !this.target?.isConnected) {
            return;
        }
        this.origin = this.target;
        const caret = caretRect(this.target);
        const box = this.target.getBoundingClientRect();
        this.pin = { x: caret.left - box.left, y: caret.top - box.top, width: box.width, height: caret.height, host: typeHost(this.target),
            scrollLeft: this.target.scrollLeft, scrollTop: this.target.scrollTop };
        this.destination = captureTarget(this.target);
        const capture = markRaw(new VoiceCapture());
        this.capture = capture;
        this.setMode("starting");
        try {
            await capture.start(() => this.stop());
            if (this.capture === capture) {
                this.setMode("recording");
                this.listen(capture);
            }
        } catch {
            if (this.capture === capture) {
                this.reset();
                this.notify.add(_t("Check microphone access and try again."), { type: "warning" });
            }
        }
    }
    async transcribe(file, signal, preview = false) {
        return requestDictation(file, signal, preview);
    }
    /**
     * Write the last words heard in the field while the writer is still speaking: without it
     * they talk into silence until the end. One request at a time, on the last seconds only.
     */
    async listen(capture) {
        const listening = new AbortController();
        this.listening = listening;
        const live = () => this.capture === capture && this.state.mode === "recording" && !listening.signal.aborted;
        while (live()) {
            await new Promise((resolve) => setTimeout(resolve, 2000));
            const file = live() && capture.tail();
            if (!file) {
                continue;
            }
            try {
                const text = await this.transcribe(file, listening.signal, true);
                if (live() && text) {
                    this.state.preview = text;
                    this.settle();
                }
            } catch {
                // The preview is a comfort: the dictation itself is unaffected.
            }
        }
    }
    async stop() {
        if (this.state.mode !== "recording") {
            return;
        }
        this.listening?.abort();
        const file = this.capture.finish();
        this.capture = null;
        if (!file) {
            this.reset();
            return;
        }
        await this.hold(file);
        await this.retry();
    }
    /** Send the kept recording for transcription again: the one on screen, or an unused one by its id. */
    async retry(id) {
        const entry = id && this.state.stored.find((stored) => stored.id === id);
        if (entry) {
            if (this.state.mode !== "idle" || this.pendingAudio || this.state.recovered) {
                return;
            }
            this.state.stored = this.state.stored.filter((stored) => stored !== entry);
            if (entry.kind === "voice_note") {
                await this.attach(entry);
                return;
            }
            ownRecording(entry.id);
            this.recordingId = entry.id;
            this.pendingAudio = entry.blob;
            this.recordedAt = entry.createdAt;
            this.persisted = true;
            this.destination = null;
            this.pin = null;
        }
        if (!this.pendingAudio || this.request) {
            return;
        }
        if (this.state.mode === "retry" && this.destination && this.target?.isConnected) {
            // The retry button disappears while processing. Return focus so
            // the result can be inserted at the selection captured earlier.
            this.target.focus({ preventScroll: true });
        }
        this.setMode("processing");
        const request = new AbortController();
        this.request = request;
        try {
            const text = await this.transcribe(this.pendingAudio, request.signal);
            if (this.request !== request) {
                return;
            }
            this.state.reason = "busy";
            if (!text) {
                // The audio is still the writer's: it may be quiet speech or a bad microphone.
                this.state.reason = "silent";
            } else if (this.destination?.insert(text)) {
                this.release();
            } else {
                // The text waits in the panel, and the audio is kept until it has been copied.
                this.state.recovered = text;
            }
        } catch {
            // The recording stays for Retry and Download, in this tab and in this browser.
        } finally {
            if (this.request === request) {
                this.request = null;
                this.state.preview = "";
                this.setMode(this.pendingAudio && !this.state.recovered ? "retry" : "idle");
            }
        }
    }
    /** The writer stops waiting for the worker: the recording stays, ready for Retry. */
    stopWaiting() {
        if (this.state.mode !== "processing" || !this.request) {
            return;
        }
        this.request.abort();
        this.request = null;
        this.state.preview = "";
        this.state.reason = "busy";
        this.setMode("retry");
    }
    /** An unused voice note goes where it was recorded: the attachments of its record. */
    async attach(entry) {
        ownRecording(entry.id);
        if (await uploadVoiceNote(this.env, entry.destination, entry.blob)) {
            await forgetRecording(entry.id);
            this.notify.add(_t("Voice note attached to %s.", entry.destination.name), { type: "success" });
        } else {
            this.notify.add(_t("The voice note could not be attached. Your recording is kept."), { type: "warning" });
            disownRecording(entry.id);
        }
    }
    get storedBody() {
        const [entry, ...others] = this.state.stored;
        const text = entry?.kind === "voice_note"
            ? _t("It was not attached to %s. Your recording is kept.", entry.destination.name)
            : _t("Your recording is kept. Transcribe it again, or download it.");
        return others.length ? `${text} ${_t("Other recordings are waiting.")}` : text;
    }
    get storedTitle() {
        const entry = this.state.stored[0];
        if (!entry) {
            return "";
        }
        const time = recordingTime(entry.createdAt);
        return entry.kind === "voice_note"
            ? _t("Unused voice note from %s", time)
            : _t("Unused dictation from %s", time);
    }
    async copyText() {
        try {
            await navigator.clipboard.writeText(this.state.recovered);
            this.release();
            this.reset();
            this.notify.add(_t("Transcript copied."), { type: "success" });
        } catch {
            this.notify.add(_t("Select the transcript and copy it."), { type: "info" });
        }
    }
}
registry.category("main_components").add("usl_voice.FieldDictation", { Component: FieldDictation });
