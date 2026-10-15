import { SearchModel } from '@web/search/search_model';

/** Restore only requested property metadata before native portable Group By validation. */
export class TaskSwimlaneSearchModel extends SearchModel {
    async load(config) {
        const loadNative = async (options) => {
            await super.load(options);
            // Native URL precedence skips default favorite activation and also
            // skips creating its menu items. Offer only already-loaded native
            // profiles without changing the current query or its precedence.
            if (!Object.values(this.searchItems).some((item) => item.type === 'favorite')) {
                this._createGroupOfFavorites(this.irFilters || []);
            }
        };
        let requested;
        try { requested = JSON.parse(config.urlState?.groupBy || '[]'); }
        catch { return loadNative(config); }
        if (!Array.isArray(requested)) return loadNative(config);
        const names = [...new Set(requested.filter((spec) => typeof spec === 'string')
            .map((spec) => spec.split(':')[0]).filter((name) => /^[^.]+\.[^.]+$/.test(name)))];
        if (!names.length) return loadNative(config);

        let { searchViewFields, searchViewArch, irFilters } = config;
        if (config.searchViewId !== undefined && (!searchViewFields || !searchViewArch || (!irFilters && config.loadIrFilters))) {
            const result = await this.viewService.loadViews({
                context: config.context || {}, resModel: config.resModel, views: [[config.searchViewId, 'search']],
            }, { actionId: this.env.config.actionId, embeddedActionId: this.env.config.currentEmbeddedActionId,
                loadIrFilters: config.loadIrFilters || false });
            searchViewFields ||= result.fields;
            searchViewArch ||= result.views.search.arch;
            irFilters ||= result.views.search.irFilters;
        }
        const fields = { ...searchViewFields };
        for (const name of names) {
            const field = fields[name.split('.')[0]];
            // The caller's native view fields determine which properties field is available.
            if (field?.type !== 'properties') continue;
            delete fields[name];
            let definition;
            try { definition = await this.orm.call(config.resModel, 'get_kanban_property_definition', [name], { context: config.context }); }
            catch (error) {
                if (!['odoo.exceptions.AccessError', 'builtins.ValueError'].includes(error.data?.name)) throw error;
            }
            if (!definition?.type || ['html', 'separator'].includes(definition.type)) continue;
            fields[name] = { name, propertyName: definition.name, tags: definition.tags, readonly: false, relation: definition.comodel, required: false,
                searchable: false, selection: definition.selection, sortable: true, store: true,
                string: definition.string, type: definition.type, relatedPropertyField: field };
        }
        return loadNative({ ...config, searchViewFields: fields, searchViewArch, irFilters });
    }

    createNewGroupBy(name, options) {
        const id = this.nextId;
        super.createNewGroupBy(name, options);
        const propertyField = this.searchViewFields[name]?.relatedPropertyField;
        if (propertyField?.type === 'properties') {
            // Reopening the native Properties menu must toggle the same item,
            // rather than creating a second copy of the restored grouping.
            Object.assign(this.searchItems[id], { isProperty: true, propertyFieldName: propertyField.name,
                name: `group_by_${name}`, definitionRecordName: propertyField.string });
        }
    }

    async _fetchPropertiesDefinition(...args) {
        const result = await super._fetchPropertiesDefinition(...args);
        const fieldName = args[1];
        for (const parent of result) for (const definition of parent.definitions) {
            const item = Object.values(this.searchItems).find((item) => item.isProperty &&
                item.fieldName === `${fieldName}.${definition.name}` && item.definitionRecordId === undefined);
            if (item) Object.assign(item, { definitionRecordId: parent.definitionRecordId,
                definitionRecordName: parent.definitionRecordName });
        }
        return result;
    }
}
