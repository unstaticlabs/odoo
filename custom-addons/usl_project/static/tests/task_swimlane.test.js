import { beforeEach, expect, test } from '@odoo/hoot';
import { waitFor } from '@odoo/hoot-dom';
import { contains, mountView, onRpc, MockServer, fields } from '@web/../tests/web_test_helpers';
import { defineProjectModels, projectModels } from '@project/../tests/project_models';
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


test('deleted property grouping recovers through its native definition accessor', async () => {
    projectModels.ProjectTask._fields.task_properties = fields.Properties({ definition_record: 'project_id', definition_record_field: 'task_properties_definition' });
    projectModels.ProjectProject._fields.task_properties_definition = fields.PropertiesDefinition();
    onRpc('get_property_definition', ({ args }) => { expect(args).toEqual(['task_properties.qa_deleted']); expect.step('native definition'); return false; });
    await mountView({ resModel: 'project.task', type: 'kanban', groupBy: ['stage_id', 'task_properties.qa_deleted'], domain: [['id', '=', 1]],
        arch: `<kanban js_class="usl_task_swimlanes"><field name="stage_id"/><templates><t t-name="card"><field name="name"/></t></templates></kanban>` });
    expect.verifySteps(['native definition']);
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
