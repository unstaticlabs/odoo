import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { BadgeField, badgeField } from "@web/views/fields/badge/badge_field";

export class MatchingBadgeField extends BadgeField {
    static template = "rebuild_account_migration.MatchingBadgeField";

    setup() {
        this.orm = useService("orm");
        this.actionService = useService("action");
    }

    async openMatchingItems() {
        const action = await this.orm.call(
            this.props.record.resModel,
            "action_rebuild_open_matching_items",
            [[this.props.record.resId]],
        );
        await this.actionService.doAction(action);
    }

    get helpText() {
        return _t(
            "Open all journal items matched under %s",
            this.formattedValue,
        );
    }
}

export const matchingBadgeField = {
    ...badgeField,
    component: MatchingBadgeField,
};

registry.category("fields").add(
    "rebuild_matching_badge",
    matchingBadgeField,
);
