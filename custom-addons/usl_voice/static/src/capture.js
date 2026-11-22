import { loadLamejs } from "@mail/discuss/voice_message/common/voice_message_service";
import { Mp3Encoder } from "@mail/discuss/voice_message/common/mp3_encoder";

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
            this.processor.connect(this.context.destination);
            this.timer = setTimeout(onLimit, 300_000);
        } catch (error) {
            this.discard();
            throw error;
        }
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
        this.processor?.disconnect();
        if (this.context && this.context.state !== "closed") {
            this.context.close().catch(() => {});
        }
        this.encoder = null;
    }
}
