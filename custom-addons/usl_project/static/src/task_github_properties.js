import { registry } from "@web/core/registry";
import { _t } from "@web/core/l10n/translation";
import { PropertiesField, propertiesField } from "@web/views/fields/properties/properties_field";
import { CardPropertiesField, cardPropertiesField } from "@web/views/fields/properties/card_properties_field";

export function isLegacyGithubProperty(property) {
    return property.type === "char" && (property.string || "").trim().toLowerCase() === "github";
}

function isVerifiedDuplicate(property, canonical) {
    if (!isLegacyGithubProperty(property) || typeof property.value !== "string" || !canonical) {
        return false;
    }
    const value = property.value.trim();
    try {
        const url = new URL(value);
        return ["http:", "https:"].includes(url.protocol) &&
            ["github.com", "www.github.com"].includes(url.hostname) &&
            !url.username && !url.password && value === canonical.trim();
    } catch {
        return false;
    }
}

// Presentation only: keep propertiesList intact because native value editing
// writes that entire array, including historical values hidden from rendering.
export class TaskPropertiesField extends PropertiesField {
    static template = "usl_project.TaskPropertiesField";

    isLegacyGithubProperty(property) {
        return isLegacyGithubProperty(property);
    }

    isVerifiedLegacyDuplicate(property) {
        return isVerifiedDuplicate(property, this.props.record.data.github_url);
    }

    legacyLabel(property) {
        return this.isLegacyGithubProperty(property) ? _t("Legacy GitHub") : property.string;
    }
}

export class TaskCardPropertiesField extends CardPropertiesField {
    static template = "usl_project.TaskCardPropertiesField";

    isLegacyGithubProperty(property) {
        return isLegacyGithubProperty(property);
    }

    isVerifiedLegacyDuplicate(property) {
        return isVerifiedDuplicate(property, this.props.record.data.github_url);
    }
}

registry.category("fields").add("usl_task_properties", { ...propertiesField, component: TaskPropertiesField, additionalClasses: ["o_field_properties"], fieldDependencies: [{ name: "github_url", type: "char" }] });
registry.category("fields").add("kanban.usl_task_properties", { ...cardPropertiesField, component: TaskCardPropertiesField, additionalClasses: ["o_field_properties"], fieldDependencies: [{ name: "github_url", type: "char" }] });
