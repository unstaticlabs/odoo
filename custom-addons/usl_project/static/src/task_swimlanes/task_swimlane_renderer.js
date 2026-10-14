import { Component, onPatched } from '@odoo/owl';
import { useState } from '@web/owl2/utils';
import { ProjectTaskKanbanRenderer } from '@project/views/project_task_kanban/project_task_kanban_renderer';
import { Dialog } from '@web/core/dialog/dialog';
import { useService } from '@web/core/utils/hooks';
import { FormViewDialog } from '@web/views/view_dialogs/form_view_dialog';
import { evaluateExpr } from '@web/core/py_js/py';
import { _t } from '@web/core/l10n/translation';
import { buildMatrix, groupValue } from './task_swimlane_matrix';

class MoveTaskDialog extends Component {
    static template = 'usl_project.MoveTaskDialog';
    static components = { Dialog };
    static props = ['close', 'record', 'source', 'columns', 'rows', 'move', 'reason', 'open', 'afterMove'];
    setup() {
        this.state = useState({ column: this.props.source.column.id, row: this.props.source.rowKey, pending: false });
    }
    get title() { return _t('Move task'); }
    get target() {
        return { column: this.props.columns.find((g) => g.id === this.state.column), row: this.props.rows.find((r) => r.key === this.state.row).group };
    }
    get reason() { return this.props.reason(this.props.record, this.props.source, this.target); }
    async move() {
        if (this.state.pending) return;
        this.state.pending = true;
        try { const target = this.target; await this.props.move(this.props.record, this.props.source, target); this.props.close(); this.props.afterMove(this.props.record.resId, target); }
        finally { this.state.pending = false; }
    }
}

export class TaskSwimlaneRenderer extends ProjectTaskKanbanRenderer {
    static template = 'usl_project.TaskSwimlaneRenderer';
    setup() {
        super.setup();
        this.notification = useService('notification');
        onPatched(() => {
            const destination = this.state.moveDestination;
            if (!destination || !this.matrixMode) return;
            const cell = [...this.cells.values()].find((c) => JSON.stringify([groupValue(c.column), groupValue(c.row)]) === destination.values);
            const record = cell?.leaf?.list.records.find((r) => r.resId === destination.resId);
            const selector = record ? `.o_kanban_record[data-id="${record.id}"]` : cell ? `.o_usl_lane_cell[data-id="${CSS.escape(cell.id)}"]` : null;
            const element = selector && this.rootRef.el.querySelector(selector);
            if (element) {
                element.scrollIntoView({ block: 'nearest', inline: 'nearest' });
                element.focus();
                if (!record) this.notification.add(_t('Task moved. Open the destination or adjust filters to find it.'), { type: 'info' });
            } else {
                this.rootRef.el.focus();
                this.notification.add(_t('Task moved. Open the destination or adjust filters to find it.'), { type: 'info' });
            }
            this.state.moveDestination = null;
        });
    }
    afterMove(resId, target) {
        this.state.moveDestination = { resId, values: JSON.stringify([groupValue(target.column), groupValue(target.row)]) };
    }
    get matrixMode() { return this.props.list.groupBy?.length === 2; }
    get rows() { return this.matrixMode ? buildMatrix(this.props.list).map((r) => ({ ...r, cells: r.cells.map((c) => ({ ...c, rowKey: r.key })) })) : []; }
    get cells() { return new Map(this.rows.flatMap((r) => r.cells).map((c) => [c.id, c])); }
    get canMoveRecords() { return this.matrixMode ? !this.props.list.model.useSampleModel && !this.props.readonly && this.props.archInfo.activeActions.edit : super.canMoveRecords; }
    get canResequenceRecords() { return super.canResequenceRecords && (!this.matrixMode || (!this.props.readonly && this.props.archInfo.activeActions.edit)); }
    get unavailableGrouping() { return this.props.list.model.unavailableGrouping; }
    get canResequenceGroups() { return this.matrixMode ? false : super.canResequenceGroups; }
    get cardGroupField() {
        const fields = this.props.list.groupBy.map((spec) => this.props.list.fields[spec.split(':')[0]]);
        // Native cards suppress deletion in a membership grouping; retain that
        // boundary when either displayed axis is many-to-many.
        return fields.find((field) => field?.type === 'many2many') || fields[0];
    }
    get axisCaption() {
        const fields = this.props.list.fields;
        return this.props.list.groupBy.map((spec) => fields[spec.split(':')[0]]?.string || spec);
    }
    moveReason(record, source, target) {
        for (const [index, spec] of this.props.list.groupBy.entries()) {
            const from = groupValue(index ? source.row : source.column);
            const to = groupValue(index ? target.row : target.column);
            if (JSON.stringify([typeof from, from]) === JSON.stringify([typeof to, to])) continue;
            const name = spec.split(':')[0];
            const field = this.props.list.fields[name];
            if (!field || spec.includes(':') || spec.includes('.') || field.readonly || !['char', 'boolean', 'integer', 'selection', 'many2one', 'many2many'].includes(field.type)) {
                return _t('Edit this grouping value in the task form.');
            }
            const nodes = Object.values(this.props.archInfo.fieldNodes).filter((node) => node.name === name);
            for (const node of nodes) {
                try { if (node.readonly && evaluateExpr(node.readonly, record.evalContext)) return _t('This field is read-only. Edit the task in its form.'); }
                catch { return _t('Open the task form to check this field before moving it.'); }
            }
        }
        return '';
    }
    openMove(record, source) {
        this.dialog.add(MoveTaskDialog, { record, source, columns: this.props.list.groups, rows: this.rows,
            move: this.move.bind(this), reason: this.moveReason.bind(this), afterMove: this.afterMove.bind(this), open: () => this.props.openRecord(record) });
    }
    async move(record, source, target) {
        if (this.moveReason(record, source, target)) return this.props.openRecord(record);
        this.toggleProcessing(record.id, true);
        try { await this.props.list.model.moveCell(record, source, target); }
        finally { this.toggleProcessing(record.id, false); }
    }
    async sortRecordDrop(recordId, sourceId, { parent, previous }) {
        if (!this.matrixMode) return super.sortRecordDrop(...arguments);
        const source = this.cells.get(sourceId), target = this.cells.get(parent?.dataset.id);
        const record = source?.leaf?.list.records.find((r) => r.id === recordId);
        if (!record || !target) return;
        parent.classList.remove('o_kanban_hover');
        if (source.id === target.id) {
            while (previous && !previous.dataset.id) previous = previous.previousElementSibling;
            return source.leaf.list._resequence(source.leaf.list.records, 'project.task', recordId, previous?.dataset.id || null);
        }
        if (this.moveReason(record, source, target)) {
            await this.props.list.model.load();
            return this.props.openRecord(record);
        }
        return this.move(record, source, target);
    }
    async openCell(cell) {
        if (cell.column.isFolded) { await cell.column.toggle(); await this.props.list.model.load(); }
        else if (cell.leaf?.isFolded) await cell.leaf.toggle();
        else await this.moreLanes(cell.column);
    }
    async moreLanes(column) {
        await column.list.load({ limit: column.list.groups.length + column.model.constructor.DEFAULT_GROUP_LIMIT });
    }
    moreColumns() {
        return this.props.list.load({ limit: this.props.list.groups.length + this.props.list.model.constructor.DEFAULT_GROUP_LIMIT });
    }
    cellContext(cell) {
        const context = { ...this.props.list.context };
        for (const [index, spec] of this.props.list.groupBy.entries()) {
            const group = index ? cell.row : cell.column;
            const field = this.props.list.fields[spec.split(':')[0]];
            const key = `default_${spec.split(':')[0]}`;
            delete context[key];
            if (!spec.includes(':') && !spec.includes('.') && !field.readonly && ['char', 'boolean', 'integer', 'selection', 'many2one', 'many2many'].includes(field.type)) {
                context[key] = field.type === 'many2many' ? (groupValue(group) ? [groupValue(group)] : []) : groupValue(group);
            }
        }
        return context;
    }
    exactDefaults(cell) {
        return this.props.list.groupBy.every((spec) => {
            const field = this.props.list.fields[spec.split(':')[0]];
            return !spec.includes(':') && !spec.includes('.') && !field?.readonly && ['char', 'boolean', 'integer', 'selection', 'many2one', 'many2many'].includes(field?.type);
        });
    }
    async createCell(cell) {
        if (cell.leaf && !cell.leaf.isFolded && this.props.canQuickCreate && this.exactDefaults(cell)) {
            await this.props.quickCreateState.openQuickCreate(cell.leaf.id);
        } else {
            this.dialog.add(FormViewDialog, { resModel: 'project.task', context: this.cellContext(cell), title: _t('New Task'), onRecordSaved: () => this.props.list.model.load() });
        }
    }
    async validateQuickCreate(recordId, mode, group) {
        if (!this.matrixMode) return super.validateQuickCreate(...arguments);
        await super.validateQuickCreate(recordId, mode, group);
        await this.props.list.model.load();
    }
    createColumn(column) {
        const context = { ...this.props.list.context };
        const spec = this.props.list.groupBy[0];
        const field = column.groupByField;
        if (!spec.includes(':') && !spec.includes('.') && !field.readonly && ['char', 'boolean', 'integer', 'selection', 'many2one', 'many2many'].includes(field.type)) {
            context[`default_${spec}`] = field.type === 'many2many' ? (groupValue(column) ? [groupValue(column)] : []) : groupValue(column);
        }
        this.dialog.add(FormViewDialog, { resModel: 'project.task', context, title: _t('New Task'), onRecordSaved: () => this.props.list.model.load() });
    }
}
