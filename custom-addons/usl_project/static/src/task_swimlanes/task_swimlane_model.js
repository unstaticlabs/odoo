import { _t } from '@web/core/l10n/translation';
import { ProjectTaskKanbanModel } from '@project/views/project_task_kanban/project_task_kanban_model';
import { groupKey, groupValue } from './task_swimlane_matrix';

export class TaskSwimlaneModel extends ProjectTaskKanbanModel {
    hasData() {
        // Like native empty stage columns, a matrix's real empty cells are useful
        // content. Do not replace them with fabricated sample memberships.
        return this.unavailableGrouping || this.matrixMode || super.hasData();
    }
    _getNextConfig(current, params) {
        const config = super._getNextConfig(current, params);
        // Native Project adds this derived context during its grouped request.
        // Normalize it before tracking scope so the first reload preserves folds.
        if (config.groupBy.length === 2) {
            config.context.project_kanban = true;
            config.context.usl_task_swimlanes = true;
        } else {
            delete config.context.usl_task_swimlanes;
        }
        const scope = JSON.stringify([config.groupBy, config.domain, config.context]);
        if (this.laneScope && scope !== this.laneScope) {
            for (const column of Object.values(config.groups || {})) {
                for (const leaf of Object.values(column.list.groups || {})) delete leaf.uslLaneInitialized;
            }
        }
        if (scope !== this.laneScope) {
            this.laneScope = scope;
            this.initializedLeaves = new Set();
        }
        this.matrixMode = config.groupBy.length === 2;
        this.initialGroupsLimit = this.matrixMode ? this.constructor.DEFAULT_GROUP_LIMIT : Number.MAX_SAFE_INTEGER;
        if (this.matrixMode && (!current.groupBy || current.groupBy.length !== 2 || current.domain !== config.domain)) {
            config.limit = this.constructor.DEFAULT_GROUP_LIMIT;
        } else if (!this.matrixMode && current.groupBy?.length === 2) {
            config.limit = Number.MAX_SAFE_INTEGER;
        }
        return config;
    }

    async _loadData(config, cache) {
        this.unavailableGrouping = false;
        if (config.groupBy?.length === 2) {
            const requested = [...config.groupBy];
            for (const spec of requested) {
                const name = spec.split(':')[0];
                if (!config.fields[name.split('.')[0]]) {
                    this.unavailableGrouping = true;
                    break;
                }
                if (name.includes('.') && !config.fields[name]) {
                    try {
                        await super._getPropertyDefinition(config, name);
                    } catch (error) {
                        if (error.data?.name !== 'odoo.exceptions.AccessError') throw error;
                        this.unavailableGrouping = true;
                    }
                    if (!config.groupBy) this.unavailableGrouping = true;
                    if (this.unavailableGrouping) break;
                }
            }
            if (this.unavailableGrouping) {
                // Keep native search facets and caller filters intact. No field
                // metadata is guessed: ordinary results let the user remove
                // the unavailable grouping through the existing search controls.
                config.groupBy = [];
                config.groups = {};
                config.limit = this.initialLimit;
                config.offset = 0;
                delete config.context.usl_task_swimlanes;
                this.matrixMode = false;
            }
        }
        return super._loadData(config, cache);
    }

    _webReadGroup(config, cache) {
        if (config.groupBy.length === 2 && !config.sendOpeningInfo) {
            // Native first discovery uses this limit for subgroups as well as
            // records. No leaves open on that request. Its parameters are built
            // synchronously; restore before awaiting so other loads keep40.
            const recordLimit = this.initialLimit;
            this.initialLimit = this.constructor.DEFAULT_GROUP_LIMIT;
            try { return super._webReadGroup(config, cache); }
            finally { this.initialLimit = recordLimit; }
        }
        return super._webReadGroup(config, cache);
    }

    async _loadGroupedList(config, cache) {
        if (this.matrixMode && Object.keys(config.groups || {}).length) config.sendOpeningInfo = true;
        const result = await super._loadGroupedList(config, cache);
        if (!this.matrixMode) return result;
        let changed = false;
        const batches = config.groupBy.length === 2 ? result.groups.filter((column) => !config.groups[column.value].isFolded).map((column) => ({
            groups: column.groups, config: config.groups[column.value].list,
        })) : [{ groups: result.groups, config }];
        for (const batch of batches) {
            let budget = this.constructor.DEFAULT_OPEN_GROUP_LIMIT;
            const spec = batch.config.groupBy[0];
            for (const leaf of batch.groups) {
                const key = JSON.stringify([batch.config.domain, groupKey(spec, { _rawValue: leaf.rawValue })]);
                const leafConfig = batch.config.groups[leaf.value];
                if (this.initializedLeaves.has(key) || leafConfig.uslLaneInitialized) continue;
                this.initializedLeaves.add(key);
                // Native local action state carries this transient flag with
                // its fold configuration, including a browser Back restoration.
                leafConfig.uslLaneInitialized = true;
                if (budget-- <= 0) continue;
                leafConfig.isFolded = false;
                leafConfig.list.limit = this.initialLimit;
                changed = true;
            }
        }
        if (!changed) return result;
        config.sendOpeningInfo = true;
        return super._loadGroupedList(config, cache);
    }

    async _postprocessReadGroup(config, response) {
        const result = await super._postprocessReadGroup(config, response);
        if (!this.matrixMode) return result;
        const relationGroups = new Map();
        const collect = (groups, spec) => {
            const field = config.fields[spec.split(':')[0]];
            if (field?.relation) {
                const entries = relationGroups.get(field.relation) || [];
                entries.push(...groups.filter((g) => typeof g.value === 'number' && g.value > 0).map((g) => ({ group: g, field })));
                relationGroups.set(field.relation, entries);
            }
        };
        collect(result.groups, config.groupBy[0]);
        if (config.groupBy[1]) {
            collect(result.groups.flatMap((column) => column.groups || []), config.groupBy[1]);
        }
        for (const [relation, entries] of relationGroups) {
            let names;
            try {
                names = await this.orm.searchRead(relation, [['id', 'in', [...new Set(entries.map(({ group }) => group.value))]]], ['display_name'], { context: { ...config.context, active_test: false } });
            } catch (error) {
                if (error.data?.name !== 'odoo.exceptions.AccessError') throw error;
                names = [];
            }
            const labels = new Map(names.map((record) => [record.id, record.display_name]));
            for (const { group } of entries) group.displayName = labels.get(group.value) || _t('Unavailable');
        }
        return result;
    }

    async moveCell(record, source, target) {
        return this.mutex.exec(async () => {
            try {
                await this.orm.call('project.task', 'action_move_kanban_cell', [[record.resId], this.root.groupBy.map((field, index) => ({
                    field, source: groupValue(index ? source.row : source.column), target: groupValue(index ? target.row : target.column),
                })), record.data.write_date || false], { context: this.root.context });
            } finally {
                await this.load();
            }
        });
    }
}
