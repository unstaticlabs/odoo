import { loadLamejs } from "@mail/discuss/voice_message/common/voice_message_service";
import { Mp3Encoder } from "@mail/discuss/voice_message/common/mp3_encoder";

export const VOICE_LIMIT_SECONDS = 300;

/** The same compressed format as Discuss, with explicit commit and discard. */
export class VoiceCapture {
    async start(onLimit) {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        if (this.closed) {
            stream.getTracks().forEach((track) => track.stop());
            return;
        }
        this.stream = stream;
        try {
            this.context = new AudioContext();
            await this.context.resume();
            await Promise.all([
                loadLamejs(),
                this.context.audioWorklet.addModule("/discuss/voice/worklet_processor"),
            ]);
            if (this.closed) {
                return;
            }
            this.encoder = new Mp3Encoder({ sampleRate: this.context.sampleRate, bitRate: 128 });
            this.processor = new AudioWorkletNode(this.context, "processor");
            this.processor.port.onmessage = ({ data }) => {
                if (!this.closed && data?.length) {
                    this.encoder.encode(data);
                }
            };
            this.source = this.context.createMediaStreamSource(stream);
            this.source.connect(this.processor);
            this.analyser = this.context.createAnalyser();
            this.analyser.fftSize = 256;
            this.samples = new Uint8Array(this.analyser.fftSize);
            this.source.connect(this.analyser);
            this.startedAt = Date.now();
            this.processor.connect(this.context.destination);
            this.timer = setTimeout(onLimit, VOICE_LIMIT_SECONDS * 1000);
        } catch (error) {
            this.discard();
            throw error;
        }
    }
    /** Seconds recorded so far. */
    get elapsed() {
        return this.startedAt ? Math.floor((Date.now() - this.startedAt) / 1000) : 0;
    }
    /** Current input loudness from 0 to 1, so the writer can see the microphone hears them. */
    level() {
        if (this.closed || !this.analyser) {
            return 0;
        }
        this.analyser.getByteTimeDomainData(this.samples);
        let sum = 0;
        for (const sample of this.samples) {
            sum += (sample - 128) ** 2;
        }
        return Math.min(1, (Math.sqrt(sum / this.samples.length) / 128) * 4);
    }
    finish() {
        const buffers = this.encoder?.finish() || [];
        this.discard();
        return buffers.length ? new File(buffers, "Voice.mp3", { type: "audio/mpeg" }) : null;
    }
    discard() {
        this.closed = true;
        clearTimeout(this.timer);
        this.stream?.getTracks().forEach((track) => track.stop());
        this.source?.disconnect();
        this.analyser = null;
        this.processor?.disconnect();
        if (this.context && this.context.state !== "closed") {
            this.context.close().catch(() => {});
        }
        this.encoder = null;
    }
}
