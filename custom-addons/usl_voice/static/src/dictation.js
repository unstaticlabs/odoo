import { Component, onWillUnmount } from "@odoo/owl";
import { useExternalListener, useState } from "@web/owl2/utils";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { VoiceCapture } from "./capture";
import { captureTarget, dictationTarget } from "./dictation_target";

export class FieldDictation extends Component {
    static template = "usl_voice.FieldDictation";
    static props = {};
    setup() {
        this.notify = useService("notification");
        this.state = useState({ mode: "idle", visible: false, x: 0, y: 0, recovered: "" });
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
            if (this.state.mode !== "idle" && this.target?.contains(ev.target)) {
                this.destination = null;
            }
        });
        useExternalListener(document, "focusout", (event) => {
            if (!event.relatedTarget && this.state.mode === "idle") {
                this.setTarget(null);
            }
        });
        useExternalListener(window, "resize", () => this.position());
        useExternalListener(document, "scroll", () => this.position(), true);
        useExternalListener(window, "pagehide", () => this.cancel());
        onWillUnmount(() => {
            this.cancel();
            this.target?.classList.remove("o-usl-dictation-target");
        });
    }
    setTarget(element) {
        this.target?.classList.remove("o-usl-dictation-target");
        this.target = element;
        this.target?.classList.add("o-usl-dictation-target");
        this.position();
    }
    position() {
        const box = this.target?.isConnected && this.target.getBoundingClientRect();
        if (box && box.width && box.height) {
            this.state.x = Math.max(2, Math.min(innerWidth - 4, box.right - 3));
            this.state.y = Math.max(2, Math.min(innerHeight - 36, box.top + (Math.min(box.height, 40) - 32) / 2));
        }
        this.state.visible = Boolean(box && box.width && box.height &&
            box.top >= 0 && box.top < innerHeight && box.right > 0 && box.left < innerWidth &&
            navigator.mediaDevices?.getUserMedia);
    }
    cancel() {
        this.capture?.discard();
        this.capture = null;
        this.request?.abort();
        this.request = null;
        this.destination = null;
        this.state.mode = "idle";
        this.state.recovered = "";
    }
    async record() {
        if (this.state.mode !== "idle" || !this.target?.isConnected) {
            return;
        }
        this.destination = captureTarget(this.target);
        const capture = new VoiceCapture();
        this.capture = capture;
        this.state.mode = "starting";
        try {
            await capture.start(() => this.stop());
            if (this.capture === capture) {
                this.state.mode = "recording";
            }
        } catch {
            if (this.capture === capture) {
                this.cancel();
                this.notify.add(_t("Check microphone access and try again."), { type: "warning" });
            }
        }
    }
    async stop() {
        if (this.state.mode !== "recording") {
            return;
        }
        const file = this.capture.finish();
        this.capture = null;
        if (!file) {
            this.cancel();
            return;
        }
        this.state.mode = "processing";
        const request = new AbortController();
        this.request = request;
        try {
            const form = new FormData();
            form.append("file", file);
            form.append("csrf_token", odoo.csrf_token);
            const response = await fetch("/usl/voice/dictate", { method: "POST", body: form, signal: request.signal });
            if (!response.ok) {
                throw Error();
            }
            const { text } = await response.json();
            if (this.request !== request) {
                return;
            }
            if (text && !this.destination?.insert(text)) {
                this.state.recovered = text;
            } else if (!text) {
                this.notify.add(_t("No speech detected. Try recording again."), { type: "info" });
            }
        } catch {
            if (!request.signal.aborted) {
                this.notify.add(_t("Transcription is unavailable. Try dictating again."), { type: "warning" });
            }
        } finally {
            if (this.request === request) {
                this.request = null;
                this.state.mode = "idle";
            }
        }
    }
    async copyText() {
        try {
            await navigator.clipboard.writeText(this.state.recovered);
            this.cancel();
        } catch {
            this.notify.add(_t("Select the transcript and copy it."), { type: "info" });
        }
    }
}
registry.category("main_components").add("usl_voice.FieldDictation", { Component: FieldDictation });
