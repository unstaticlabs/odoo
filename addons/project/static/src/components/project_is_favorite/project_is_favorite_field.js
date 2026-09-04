import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import {
    BooleanFavoriteField,
    booleanFavoriteField,
} from "@web/views/fields/boolean_favorite/boolean_favorite_field";

class ProjectIsFavoriteField extends BooleanFavoriteField {
    setup() {
        super.setup();
        this.menu = useService("menu");
    }

    async update() {
        if (this.props.readonly) {
            return;
        }
        await super.update();
        if (this.props.autosave) {
            await this.menu.reload();
        }
    }
}

export const projectIsFavoriteField = {
    ...booleanFavoriteField,
    component: ProjectIsFavoriteField,
    extractProps: (fieldsInfo, dynamicInfo) => {
        return {
            ...booleanFavoriteField.extractProps(fieldsInfo, dynamicInfo),
            readonly: Boolean(fieldsInfo.attrs.readonly),
        };
    },
};

registry.category("fields").add("project_is_favorite", projectIsFavoriteField);
