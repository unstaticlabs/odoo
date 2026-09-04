import { registry } from "@web/core/registry";
import { ColorField, colorField } from "@web/views/fields/color/color_field";

export class CompanyColorField extends ColorField {
    get color() {
        return (
            this.props.record.data[this.props.name] ||
            this.props.record.data.usl_resolved_ui_theme_color ||
            ""
        );
    }
}

export const companyColorField = {
    ...colorField,
    component: CompanyColorField,
    fieldDependencies: [
        { name: "usl_resolved_ui_theme_color", type: "char" },
    ],
};

registry.category("fields").add("usl_company_color", companyColorField);
