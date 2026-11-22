import { Component, onMounted, onWillUnmount } from "@odoo/owl";
import { useState } from "@web/owl2/utils";
import { useService } from "@web/core/utils/hooks";
import { patch } from "@web/core/utils/patch";
import { Message } from "@mail/core/common/message";

export class VoiceTranscripts extends Component {
    static template = "usl_voice.Transcripts";
    static props = ["message"];
    setup() {
        this.orm = useService("orm");
        this.state = useState({ notes: [], error: false });
        onMounted(() => this.refresh());
        onWillUnmount(() => {
            this.closed = true;
            clearTimeout(this.timer);
        });
    }
    async refresh() {
        clearTimeout(this.timer);
        try {
            const [data] = await this.orm.read("mail.message", [this.props.message.id], ["usl_voice_notes"]);
            if (this.closed) {
                return;
            }
            this.state.notes = data.usl_voice_notes || [];
            this.state.error = false;
            if (this.state.notes.some((n) => n.state === "pending")) {
                this.timer = setTimeout(() => this.refresh(), 3500);
            }
        } catch {
            if (!this.closed) {
                this.state.notes = [];
                this.state.error = true;
            }
        }
    }
    async retry() {
        await this.orm.call("mail.message", "action_usl_retry_voice", [[this.props.message.id]]);
        await this.refresh();
    }
}
patch(Message, { components: { ...Message.components, VoiceTranscripts } });
