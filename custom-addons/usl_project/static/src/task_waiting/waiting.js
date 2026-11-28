import { onMounted } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { CharField, charField } from "@web/views/fields/char/char_field";
import {
    ProjectTaskStateSelection,
    projectTaskStateSelection,
} from "@project/components/project_task_state_selection/project_task_state_selection";

const WAITING = "04_waiting_normal";
const IN_PROGRESS = "01_in_progress";

/**
 * The native status menu cannot choose Waiting, and offers no way out of it.
 * Add Waiting (form only, where the reason can be typed) and, while a reason
 * is set, In Progress, which the server turns into "clear the reason".
 */
export class WaitingTaskState extends ProjectTaskStateSelection {
    static props = {
        ...ProjectTaskStateSelection.props,
        offerWaiting: { type: Boolean, optional: true },
    };

    get options() {
        const options = super.options;
        const data = this.props.record.data;
        if (data[this.props.name] === WAITING) {
            // Also while the Waiting choice is unsaved, so it can be undone.
            if (data.usl_waiting_reason || this.props.record.dirty) {
                options.unshift([IN_PROGRESS, _t("In Progress")]);
            }
        } else if (this.props.viewType === "form" && this.props.offerWaiting && !this.props.readonly) {
            options.splice(Math.max(0, options.length - 2), 0, [WAITING, _t("Waiting")]);
        }
        return options;
    }

    async updateRecord(value) {
        if (value !== WAITING) {
            // Any other status clears the reason and contact, even when the
            // reason was edited in the same save; the server enforces it too.
            const record = this.props.record;
            const changes = {};
            if (record.data.usl_waiting_reason && "usl_waiting_reason" in record.activeFields) {
                changes.usl_waiting_reason = false;
            }
            if (record.data.usl_waiting_partner_id && "usl_waiting_partner_id" in record.activeFields) {
                changes.usl_waiting_partner_id = false;
            }
            if (Object.keys(changes).length) {
                await record.update(changes);
            }
            return super.updateRecord(value);
        }
        if (this.props.viewType !== "form") {
            return super.updateRecord(value);
        }
        // Do not save a Waiting task that has no reason yet: the reason field
        // appears, takes focus, and the form saves both together.
        await this.props.record.update({ [this.props.name]: value });
    }
}

/** The reason input takes focus when Waiting was just chosen on this form. */
export class WaitingReasonField extends CharField {
    setup() {
        super.setup();
        onMounted(() => {
            if (!this.props.record.data[this.props.name] && this.props.record.dirty) {
                this.input.el?.focus();
            }
        });
    }
}

registry.category("fields").add("usl_waiting_reason", { ...charField, component: WaitingReasonField });

registry.category("fields").add(
    "project_task_state_selection",
    {
        ...projectTaskStateSelection,
        component: WaitingTaskState,
        extractProps(fieldInfo, dynamicInfo) {
            const props = projectTaskStateSelection.extractProps(fieldInfo, dynamicInfo);
            // Only forms that show the reason field (see the form inheritance) offer Waiting.
            props.offerWaiting = Boolean(fieldInfo.options.usl_offer_waiting);
            return props;
        },
        fieldDependencies: [
            ...projectTaskStateSelection.fieldDependencies,
            { name: "usl_waiting_reason", type: "char" },
        ],
    },
    { force: true },
);
