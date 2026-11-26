import { Component, onMounted, onWillUnmount } from "@odoo/owl";
import { useRef, useState } from "@web/owl2/utils";
import { _t } from "@web/core/l10n/translation";
import { VOICE_LIMIT_SECONDS } from "./capture";

/** Shows that the microphone is live: a dot that follows the voice and the elapsed time. */
export class VoiceMeter extends Component {
    static template = "usl_voice.Meter";
    static props = { capture: Object };
    setup() {
        this.dot = useRef("dot");
        this.state = useState({ seconds: 0 });
        onMounted(() => {
            this.clock = setInterval(() => (this.state.seconds = this.props.capture.elapsed), 250);
            const draw = () => {
                this.dot.el?.style.setProperty("--usl-voice-level", this.props.capture.level().toFixed(2));
                this.frame = requestAnimationFrame(draw);
            };
            draw();
        });
        onWillUnmount(() => {
            clearInterval(this.clock);
            cancelAnimationFrame(this.frame);
        });
    }
    /** The last thirty seconds count down so the automatic stop is no surprise. */
    get ending() {
        return this.state.seconds >= VOICE_LIMIT_SECONDS - 30;
    }
    get label() {
        return this.ending
            ? _t("%s left", formatSeconds(Math.max(0, VOICE_LIMIT_SECONDS - this.state.seconds)))
            : formatSeconds(this.state.seconds);
    }
}

export function formatSeconds(seconds) {
    return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, "0")}`;
}
