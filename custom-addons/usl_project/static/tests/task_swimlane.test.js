import { beforeEach, expect, test } from '@odoo/hoot';
import { waitFor } from '@odoo/hoot-dom';
import { contains, mountView, onRpc, MockServer, fields, mountWithSearch, toggleSearchBarMenu, toggleMenuItem, makeServerError } from '@web/../tests/web_test_helpers';
import { defineProjectModels, projectModels } from '@project/../tests/project_models';
import { SearchBar } from '@web/search/search_bar/search_bar';
import { TaskSwimlaneSearchModel } from '../src/task_swimlanes/task_swimlane_search_model';
import { buildMatrix, groupKey } from '../src/task_swimlanes/task_swimlane_matrix';

defineProjectModels();
beforeEach(() => {
    projectModels.ProjectTask._records = [
        { id: 1, name: 'Amber task', stage_id: 1, priority: '0' },
        { id: 2, name: 'Cobalt task', stage_id: 2, priority: '1' },
    ];
});

test('native nested opening shows initial cards under shared column headers', async () => {
    let requests = [];
    onRpc('web_read_group', ({ kwargs }) => { requests.push(kwargs); });
    await mountView({ resModel: 'project.task', type: 'kanban', groupBy: ['stage_id', 'priority'],
        arch: `<kanban js_class="usl_task_swimlanes"><field name="stage_id"/><field name="priority"/><templates><t t-name="card"><field name="name"/></t></templates></kanban>` });
    expect('.o_usl_column_heading').toHaveCount(2);
    expect('.o_kanban_record').toHaveCount(2);
    expect([...document.querySelectorAll('.o_kanban_record')].map((card) => card.textContent.trim()).sort()).toEqual(['Amber task', 'Cobalt task']);
    expect(requests[0].limit).toBe(80);
    expect(requests.some((r) => r.opening_info?.some((column) => column.groups?.some((leaf) => leaf.folded === false)))).toBe(true);
    expect('.o_usl_lane_heading').toHaveCount(3);
});

test('one group delegates native project cards without matrix', async () => {
    await mountView({ resModel: 'project.task', type: 'kanban', groupBy: ['stage_id'],
        arch: `<kanban js_class="usl_task_swimlanes"><templates><t t-name="card"><field name="name"/></t></templates></kanban>` });
    expect('.o_usl_lane_grid').toHaveCount(0);
    expect('.o_kanban_record').toHaveCount(2);
});

test('typed sparse generated lanes never confuse duplicate labels or unloaded cells', () => {
    for (let n = 0; n < 25; n++) {
        const values = [false, 0, '', '0', n + 1];
        const leaf = (value) => ({ _rawValue: value, displayName: 'Same', isFolded: false, list: { records: [] }, count: 1 });
        const columns = [0, 1, 2].map((id) => ({ id: String(id), isFolded: false, list: {
            groups: values.filter((_v, i) => (i + id + n) % 3 !== 0).map(leaf).reverse(), count: 10,
        } }));
        const matrix = buildMatrix({ groups: columns, groupBy: ['stage_id', 'custom'] });
        expect(new Set(matrix.map((row) => row.key)).size).toBe(5);
        for (const row of matrix) for (const cell of row.cells) {
            if (cell.leaf) expect(groupKey('custom', cell.leaf)).toBe(row.key);
            else expect(cell.status).toBe('unloaded');
        }
    }
});

test('cell quick create supplies both native scalar defaults', async () => {
    await mountView({ resModel: 'project.task', type: 'kanban', groupBy: ['stage_id', 'priority'],
        arch: `<kanban js_class="usl_task_swimlanes" quick_create="1"><field name="stage_id"/><field name="priority"/><templates><t t-name="card"><field name="name"/></t></templates></kanban>` });
    onRpc('onchange', ({ kwargs }) => {
        expect(kwargs.context.default_stage_id).toBe(1);
        expect(kwargs.context.default_priority).toBe('0');
        expect.step('correct cell defaults');
    });
    await contains('.o_usl_lane_cell button[title="New task in this cell"]:first').click();
    await waitFor('.o_kanban_quick_create');
    expect('.o_kanban_quick_create').toHaveCount(1);
    expect.verifySteps(['correct cell defaults']);
});

test('an empty matrix retains real groups and never invents sample memberships', async () => {
    projectModels.ProjectTask._records = [];
    await mountView({ resModel: 'project.task', type: 'kanban', groupBy: ['stage_id', 'priority'],
        arch: `<kanban js_class="usl_task_swimlanes" sample="1"><field name="stage_id"/><field name="priority"/><templates><t t-name="card"><field name="name"/></t></templates></kanban>` });
    expect('.o_kanban_record').toHaveCount(0);
    expect('.o_usl_move_task').toHaveCount(0);
});

test('native finite lane pages expose continuation rather than invent empty cells', async () => {
    const wire = [];
    onRpc('web_read_group', ({ args, kwargs }) => { wire.push({ groupBy: args[1], limit: kwargs.limit, open: kwargs.opening_info?.map((g) => ({value:g.value, folded:g.folded,limit:g.limit,groups:g.groups?.filter((r) => !r.folded).map((r) => r.value)})) }); });
    projectModels.ProjectTask._records = Array.from({ length: 85 }, (_v, i) => ({ id: i + 1, name: `Task ${String(i).padStart(2, '0')}`, stage_id: 1, priority: '0' }));
    await mountView({ resModel: 'project.task', type: 'kanban', groupBy: ['stage_id', 'name'],
        arch: `<kanban js_class="usl_task_swimlanes"><field name="stage_id"/><templates><t t-name="card"><field name="name"/></t></templates></kanban>` });
    expect('.o_usl_lane_heading').toHaveCount(81);
    expect('.o_kanban_record').toHaveCount(10);
    await contains('.o_usl_column_heading button:contains(Load more lanes)').click();
    expect('.o_usl_lane_heading').toHaveCount(86);
    // Native HOOT MockModel caps even explicitly opened groups at10; Python
    // honors explicit opening_info. The real server regression proves15 cards.
    expect(wire.some((request) => request.open?.filter((group) => !group.folded).length === 15)).toBe(true);
    expect('.o_kanban_record').toHaveCount(10);
    expect('.o_usl_column_heading button:contains(Load more lanes)').toHaveCount(0);
});

test('native leaf pagination loads task identities beyond the first forty', async () => {
    projectModels.ProjectTask._records = Array.from({ length: 45 }, (_v, i) => ({ id: i + 1, name: `Task ${i}`, stage_id: 1, priority: '0' }));
    await mountView({ resModel: 'project.task', type: 'kanban', groupBy: ['stage_id', 'priority'],
        arch: `<kanban js_class="usl_task_swimlanes"><field name="stage_id"/><field name="priority"/><templates><t t-name="card"><field name="name"/></t></templates></kanban>` });
    expect('.o_kanban_record').toHaveCount(40);
    await contains('button:contains(Load more tasks)').click();
    expect('.o_kanban_record').toHaveCount(45);
    expect(new Set([...document.querySelectorAll('.o_kanban_record')].map((card) => card.textContent.trim())).size).toBe(45);
});

test('native second-axis false relation memberships remain available to open', async () => {
    projectModels.ProjectTask._records = [{ id: 1, name: 'Unassigned task', stage_id: 1, user_ids: [] }];
    await mountView({ resModel: 'project.task', type: 'kanban', groupBy: ['stage_id', 'user_ids'],
        arch: `<kanban js_class="usl_task_swimlanes"><field name="stage_id"/><field name="user_ids"/><templates><t t-name="card"><field name="name"/></t></templates></kanban>` });
    expect('.o_kanban_record').toHaveCount(1);
    expect('.o_kanban_record').toHaveText('Unassigned task');
});

test('native card edits reflow second-axis membership without a manual reload', async () => {
    await mountView({ resModel: 'project.task', type: 'kanban', groupBy: ['stage_id', 'priority'],
        arch: `<kanban js_class="usl_task_swimlanes"><field name="stage_id"/><templates><t t-name="card"><field name="name"/><field name="priority" widget="priority"/></t></templates></kanban>` });
    await contains('.o_kanban_record:first .o_priority_star').click();
    await waitFor('.o_usl_lane_heading:contains(High)');
    expect('.o_usl_lane_heading:contains(Low)').toHaveCount(0);
    expect('.o_kanban_record').toHaveCount(2);
});

test('Move cancel leaves both grouping values unchanged', async () => {
    onRpc('action_move_kanban_cell', () => { throw new Error('Cancel must not write'); });
    await mountView({ resModel: 'project.task', type: 'kanban', groupBy: ['stage_id', 'priority'],
        arch: `<kanban js_class="usl_task_swimlanes"><field name="stage_id"/><field name="priority"/><templates><t t-name="card"><field name="name"/></t></templates></kanban>` });
    await contains('.o_usl_move_task:first').click();
    await contains('.modal-footer button:contains(Cancel)').click();
    expect('.modal').toHaveCount(0);
    const task = MockServer.env['project.task'].read([1], ['stage_id', 'priority'])[0];
    expect(task.stage_id[0]).toBe(1);
    expect(task.priority).toBe('0');
});

test('accessible Move uses one two-axis RPC and returns focus to the exact destination card', async () => {
    let writes = 0;
    onRpc('action_move_kanban_cell', ({ args }) => {
        writes++;
        expect(args[0]).toEqual([1]);
        expect(args[1]).toEqual([{ field: 'stage_id', source: 1, target: 2 }, { field: 'priority', source: '0', target: '1' }]);
        MockServer.env['project.task'].write([1], { stage_id: 2, priority: '1' });
        return true;
    });
    await mountView({ resModel: 'project.task', type: 'kanban', groupBy: ['stage_id', 'priority'],
        arch: `<kanban js_class="usl_task_swimlanes"><field name="stage_id"/><field name="priority"/><templates><t t-name="card"><field name="name"/></t></templates></kanban>` });
    await contains('.o_usl_move_task:first').click();
    await contains('#usl_move_column').select(document.querySelectorAll('#usl_move_column option')[1].value);
    await contains('#usl_move_lane').select(document.querySelectorAll('#usl_move_lane option')[1].value);
    await contains('.modal-footer button:contains(Move)').click();
    await waitFor('.o_kanban_record:contains(Amber task)');
    expect(writes).toBe(1);
    expect('.modal').toHaveCount(0);
    expect(document.activeElement.textContent.trim()).toBe('Amber task');
    expect(document.activeElement.closest('.o_usl_lane_cell').getAttribute('aria-label').includes('High')).toBe(true);
});

test('read-only board retains readable lanes without movement controls', async () => {
    await mountView({ resModel: 'project.task', type: 'kanban', groupBy: ['stage_id', 'priority'],
        arch: `<kanban js_class="usl_task_swimlanes" edit="0"><field name="stage_id"/><field name="priority"/><templates><t t-name="card"><field name="name"/></t></templates></kanban>` });
    expect('.o_kanban_record').toHaveCount(2);
    expect('.o_usl_move_task').toHaveCount(0);
    expect('.o_draggable').toHaveCount(0);
});


test('unavailable second grouping keeps filtered tasks readable and explains native recovery', async () => {
    await mountView({ resModel: 'project.task', type: 'kanban', groupBy: ['stage_id', 'qa_deleted_field'], domain: [['id', '=', 1]],
        arch: `<kanban js_class="usl_task_swimlanes"><field name="stage_id"/><templates><t t-name="card"><field name="name"/></t></templates></kanban>` });
    expect('.o_usl_grouping_recovery').toHaveCount(1);
    expect('.o_usl_lane_grid').toHaveCount(0);
    expect('.o_kanban_record:not(.o_kanban_ghost)').toHaveCount(1);
    expect('.o_kanban_record:not(.o_kanban_ghost)').toHaveText('Amber task');
});


test('deleted property grouping recovers through its caller-scoped definition accessor', async () => {
    projectModels.ProjectTask._fields.task_properties = fields.Properties({ definition_record: 'project_id', definition_record_field: 'task_properties_definition' });
    projectModels.ProjectProject._fields.task_properties_definition = fields.PropertiesDefinition();
    onRpc('get_kanban_property_definition', ({ args }) => { expect(args).toEqual(['task_properties.qa_deleted']); expect.step('scoped definition'); return false; });
    await mountView({ resModel: 'project.task', type: 'kanban', groupBy: ['stage_id', 'task_properties.qa_deleted'], domain: [['id', '=', 1]],
        arch: `<kanban js_class="usl_task_swimlanes"><field name="stage_id"/><templates><t t-name="card"><field name="name"/></t></templates></kanban>` });
    expect.verifySteps(['scoped definition']);
    expect('.o_usl_grouping_recovery').toHaveCount(1);
    expect('.o_kanban_record:not(.o_kanban_ghost)').toHaveCount(1);
    expect('.o_kanban_record:not(.o_kanban_ghost)').toHaveText('Amber task');
});


test('unavailable grouping with no matching tasks never presents sample tasks as filtered results', async () => {
    await mountView({ resModel: 'project.task', type: 'kanban', groupBy: ['stage_id', 'qa_deleted_field'], domain: [['id', '=', 999999]],
        arch: `<kanban js_class="usl_task_swimlanes" sample="1"><field name="stage_id"/><templates><t t-name="card"><field name="name"/></t></templates></kanban>` });
    expect('.o_usl_grouping_recovery').toHaveCount(1);
    expect('.o_kanban_record:not(.o_kanban_ghost)').toHaveCount(0);
});


test('unset native selection lane has a readable heading, cell label and Move option', async () => {
    projectModels.ProjectTask._records = [
        { id: 1, name: 'Amber task', stage_id: 1, priority: false },
        { id: 2, name: 'Cobalt task', stage_id: 2, priority: '1' },
    ];
    await mountView({ resModel: 'project.task', type: 'kanban', groupBy: ['stage_id', 'priority'],
        arch: `<kanban js_class="usl_task_swimlanes"><field name="stage_id"/><field name="priority"/><templates><t t-name="card"><field name="name"/></t></templates></kanban>` });
    expect('.o_usl_lane_heading:contains(None)').toHaveCount(1);
    expect('.o_usl_lane_cell[aria-label="Todo / None"]').toHaveCount(1);
    expect('.o_usl_lane_cell[aria-label="Todo / None"] .o_kanban_record').toHaveText('Amber task');
    await contains('.o_usl_lane_cell[aria-label="Todo / None"] .o_usl_move_task').click();
    expect('#usl_move_lane option:contains(None)').toHaveCount(1);
    expect(document.querySelector('#usl_move_lane').selectedOptions[0].textContent).toBe('None');
});


test('unset native selection column retains its exact false identity with a readable label', async () => {
    projectModels.ProjectTask._records = [
        { id: 1, name: 'Amber task', stage_id: 1, priority: false },
        { id: 2, name: 'Cobalt task', stage_id: 2, priority: '1' },
    ];
    await mountView({ resModel: 'project.task', type: 'kanban', groupBy: ['priority', 'stage_id'],
        arch: `<kanban js_class="usl_task_swimlanes"><field name="stage_id"/><field name="priority"/><templates><t t-name="card"><field name="name"/></t></templates></kanban>` });
    expect('.o_usl_column_heading:contains(None)').toHaveCount(1);
    expect('.o_usl_lane_cell[aria-label="None / Todo"] .o_kanban_record').toHaveText('Amber task');
    await contains('.o_usl_lane_cell[aria-label="None / Todo"] .o_usl_move_task').click();
    expect(document.querySelector('#usl_move_column').selectedOptions[0].textContent).toBe('None');
});


test.tags('desktop');
test('native root header reorder keeps shared cells and task memberships aligned', async () => {
    onRpc('web_resequence', ({ model, args }) => {
        expect(model).toBe('project.task.type');
        expect(args[0]).toEqual([2, 1]);
        expect.step('native root resequence');
    });
    await mountView({ resModel: 'project.task', type: 'kanban', groupBy: ['stage_id', 'priority'],
        arch: `<kanban js_class="usl_task_swimlanes"><field name="stage_id"/><templates><t t-name="card"><field name="name"/></t></templates></kanban>` });
    expect('.o_usl_column_heading.o_group_draggable').toHaveCount(2);
    expect('.o_usl_lane_cell.o_group_draggable').toHaveCount(0);
    await contains('.o_usl_column_heading:first .o_column_title').dragAndDrop('.o_usl_column_heading:last');
    expect.verifySteps(['native root resequence']);
    expect('.o_usl_column_heading:first').toHaveText(/In Progress/);
    expect('.o_usl_lane_cell:first .o_kanban_record').toHaveText('Cobalt task');
    expect(MockServer.env['project.task'].read([1], ['stage_id'])[0].stage_id[0]).toBe(1);
});

test('selection and native groups_draggable gates prevent root header reorder', async () => {
    await mountView({ resModel: 'project.task', type: 'kanban', groupBy: ['stage_id', 'priority'],
        arch: `<kanban js_class="usl_task_swimlanes" groups_draggable="0"><templates><t t-name="card"><field name="name"/></t></templates></kanban>` });
    expect('.o_group_draggable').toHaveCount(0);
});

test('personal stages retain native column creation with the second grouping', async () => {
    projectModels.ProjectTask._fields.personal_stage_type_id = fields.Many2one({ relation: 'project.task.type', string: 'Personal Stage', store: false, groupable: false });
    projectModels.ProjectTask._records.forEach((task) => task.personal_stage_type_id = task.stage_id);
    await mountView({ resModel: 'project.task', type: 'kanban', groupBy: ['personal_stage_type_id', 'priority'], context: { my_tasks: 1 },
        arch: `<kanban js_class="usl_task_swimlanes" default_group_by="personal_stage_type_id"><field name="personal_stage_type_id"/><templates><t t-name="card"><field name="name"/></t></templates></kanban>` });
    expect('.o_column_quick_create').toHaveCount(1);
    await contains('.o_column_quick_create.o_quick_create_folded div').click();
    await contains('.o_column_quick_create input').edit('Next personal stage');
    await contains('.o_column_quick_create .o_kanban_add').click();
    await waitFor('.o_usl_column_heading:contains(Next personal stage)');
    expect('.o_usl_column_heading:contains(Next personal stage)').toHaveCount(1);
    expect('.o_kanban_record').toHaveCount(2);
});

function propertySearchConfig() {
    return { SearchModel: TaskSwimlaneSearchModel, resModel: 'project.task', searchViewId: false,
        searchViewFields: {
            stage_id: { name: 'stage_id', type: 'many2one', relation: 'project.task.type', string: 'Stage' },
            task_properties: { name: 'task_properties', type: 'properties', string: 'Properties' },
        },
        searchViewArch: `<search><filter name="stage" string="Stage" context="{'group_by':'stage_id'}"/><filter name="properties" string="Properties" context="{'group_by':'task_properties'}"/></search>`,
        urlState: { groupBy: JSON.stringify(['stage_id', 'task_properties.review_lane']) } };
}

test('native portable property grouping restores before validation without reading private project names', async () => {
    onRpc('get_kanban_property_definition', ({ args }) => {
        expect(args).toEqual(['task_properties.review_lane']);
        return { name: 'review_lane', string: 'Review lane', type: 'selection', selection: [['a', 'A'], ['b', 'B']] };
    });
    onRpc('project.project', 'web_search_read', () => { throw new Error('Restoring a task property must not read its parent name'); });
    const bar = await mountWithSearch(SearchBar, propertySearchConfig());
    expect(bar.env.searchModel.groupBy).toEqual(['stage_id', 'task_properties.review_lane']);
    expect('.o_searchview_facet').toHaveText(/Review lane/);
    expect(bar.env.searchModel.getSearchItems((item) => item.isProperty).length).toBe(1);
});

test('missing property definition preserves native partial recovery and valid first axis', async () => {
    onRpc('get_kanban_property_definition', () => ({}));
    const bar = await mountWithSearch(SearchBar, propertySearchConfig());
    expect(bar.env.searchModel.groupBy).toEqual(['stage_id']);
    expect('.o_notification').toHaveText(/Shared filters couldn't be applied/);
});

test('hidden base property field cannot be loaded by a portable URL', async () => {
    onRpc('get_kanban_property_definition', () => { throw new Error('Hidden field must not request metadata'); });
    const config = propertySearchConfig();
    delete config.searchViewFields.task_properties;
    config.searchViewArch = `<search><filter name="stage" string="Stage" context="{'group_by':'stage_id'}"/></search>`;
    const bar = await mountWithSearch(SearchBar, config);
    expect(bar.env.searchModel.groupBy).toEqual(['stage_id']);
});


test('native Properties menu reuses the restored item without a second grouping copy', async () => {
    projectModels.ProjectTask._fields.task_properties = fields.Properties({ definition_record: 'project_id', definition_record_field: 'task_properties_definition', string: 'Properties' });
    projectModels.ProjectProject._fields.task_properties_definition = fields.PropertiesDefinition();
    const definition = { name: 'review_lane', string: 'Review lane', type: 'selection', selection: [['a', 'A'], ['b', 'B']] };
    projectModels.ProjectProject._records[0].task_properties_definition = [definition];
    projectModels.ProjectProject._records[1].task_properties_definition = [];
    onRpc('get_kanban_property_definition', () => definition);
    const bar = await mountWithSearch(SearchBar, propertySearchConfig());
    await toggleSearchBarMenu();
    await contains('.o_add_custom_group_menu:contains(Properties)').click();
    await waitFor('.o_menu_item:contains(Review lane)');
    expect('.o_menu_item:contains(Review lane)').toHaveCount(1);
    expect('.o_menu_item:contains(Review lane)').toHaveAttribute('aria-checked', 'true');
    await toggleMenuItem('Review lane');
    expect(bar.env.searchModel.groupBy).toEqual(['stage_id']);
    await toggleMenuItem('Review lane');
    expect(bar.env.searchModel.groupBy).toEqual(['stage_id', 'task_properties.review_lane']);
});

test.tags('desktop');
test('native folded root title reopens its real second-axis cards', async () => {
    projectModels.ProjectTaskType._fields.fold = fields.Boolean();
    projectModels.ProjectTaskType._records[0].fold = true;
    await mountView({ resModel: 'project.task', type: 'kanban', groupBy: ['stage_id', 'priority'],
        arch: `<kanban js_class="usl_task_swimlanes"><field name="stage_id"/><templates><t t-name="card"><field name="name"/></t></templates></kanban>` });
    expect('.o_kanban_record:contains(Amber task)').toHaveCount(0);
    await contains('.o_usl_column_heading:first .o_column_title').click();
    await waitFor('.o_kanban_record:contains(Amber task)');
    expect('.o_kanban_record:contains(Amber task)').toHaveCount(1);
});


test('revoked property metadata returns to the native valid grouping without exposing the schema', async () => {
    onRpc('get_kanban_property_definition', () => { throw makeServerError({ type: 'AccessError' }); });
    const bar = await mountWithSearch(SearchBar, propertySearchConfig());
    expect(bar.env.searchModel.groupBy).toEqual(['stage_id']);
    expect(bar.env.searchModel.getSearchItems((item) => item.isProperty)).toHaveLength(0);
});

test('native saved favorite keeps property grouping and ordinary filter domain', async () => {
    onRpc('get_kanban_property_definition', () => ({ name: 'review_lane', string: 'Review lane', type: 'selection', selection: [['a', 'A']] }));
    const bar = await mountWithSearch(SearchBar, { ...propertySearchConfig(), urlState: {}, irFilters: [{ id: 1, name: 'Saved review lanes', user_ids: [1], is_default: true,
        domain: "[['priority', '=', '1']]", context: "{'group_by': ['stage_id', 'task_properties.review_lane']}", sort: '[]' }] });
    expect(bar.env.searchModel.groupBy).toEqual(['stage_id', 'task_properties.review_lane']);
    expect(bar.env.searchModel.domain).toEqual([['priority', '=', '1']]);
    expect('.o_searchview_facet').toHaveText(/Saved review lanes/);
});
