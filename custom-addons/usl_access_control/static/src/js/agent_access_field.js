/** @odoo-module **/

import { _t } from "@web/core/l10n/translation";
import { x2ManyCommands } from "@web/core/orm_service";
import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

import { Component } from "@odoo/owl";

export class AgentAccessField extends Component {
    static template = "usl_access_control.AgentAccessField";
    static props = { ...standardFieldProps };

    get hierarchy() {
        return this.props.record.data.view_group_hierarchy || {
            categories: [],
            groups: {},
            privileges: {},
        };
    }

    get categories() {
        const privileges = this.hierarchy.privileges;
        return this.hierarchy.categories
            .map((category) => ({
                ...category,
                privileges: category.privilege_ids
                    .map((id) => privileges[id])
                    .filter((privilege) => privilege?.group_ids?.length),
            }))
            .filter((category) => category.privileges.length);
    }

    get selectedGroupIds() {
        return new Set(this.props.record.data[this.props.name]?.currentIds || []);
    }

    get readOnlyGroupIds() {
        return new Set(this.props.record.data.read_only_group_ids?.currentIds || []);
    }

    selectedGroup(privilege) {
        return privilege.group_ids.findLast((id) => this.selectedGroupIds.has(id)) || false;
    }

    selectedValue(privilege) {
        const groupId = this.selectedGroup(privilege);
        if (!groupId) {
            return "none";
        }
        return `${this.readOnlyGroupIds.has(groupId) ? "read" : "write"}:${groupId}`;
    }

    groupName(groupId) {
        return this.hierarchy.groups[groupId]?.name || _t("Access");
    }

    async updatePrivilege(privilege, value) {
        if (this.props.readonly) {
            return;
        }
        const selected = this.selectedGroupIds;
        const readOnly = this.readOnlyGroupIds;
        for (const groupId of privilege.group_ids) {
            selected.delete(groupId);
            readOnly.delete(groupId);
        }
        if (value !== "none") {
            const [mode, rawGroupId] = value.split(":");
            const groupId = Number(rawGroupId);
            if (!privilege.group_ids.includes(groupId)) {
                return;
            }
            selected.add(groupId);
            if (mode === "read") {
                readOnly.add(groupId);
            }
        }
        await this.props.record.update({
            [this.props.name]: [x2ManyCommands.set([...selected])],
            read_only_group_ids: [x2ManyCommands.set([...readOnly])],
        });
    }

    async onAccessChanged(privilege, event) {
        await this.updatePrivilege(privilege, event.target.value);
    }

    async setAll(mode) {
        if (this.props.readonly) {
            return;
        }
        const selected = this.selectedGroupIds;
        const readOnly = this.readOnlyGroupIds;
        for (const category of this.categories) {
            for (const privilege of category.privileges) {
                for (const groupId of privilege.group_ids) {
                    selected.delete(groupId);
                    readOnly.delete(groupId);
                }
                const groupId = privilege.group_ids.at(-1);
                selected.add(groupId);
                if (mode === "read") {
                    readOnly.add(groupId);
                }
            }
        }
        await this.props.record.update({
            [this.props.name]: [x2ManyCommands.set([...selected])],
            read_only_group_ids: [x2ManyCommands.set([...readOnly])],
        });
    }
}

export const agentAccessField = {
    component: AgentAccessField,
    fieldDependencies: [
        { name: "view_group_hierarchy", type: "json", readonly: true },
        { name: "read_only_group_ids", type: "many2many", relation: "res.groups" },
    ],
    additionalClasses: ["w-100"],
};

registry.category("fields").add("usl_agent_access", agentAccessField);
