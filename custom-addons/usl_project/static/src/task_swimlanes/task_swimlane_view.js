import { registry } from '@web/core/registry';
import { projectTaskKanbanView } from '@project/views/project_task_kanban/project_task_kanban_view';
import { ProjectTaskKanbanController } from '@project/views/project_task_kanban/project_task_kanban_controller';
import { TaskSwimlaneSearchModel } from './task_swimlane_search_model';
import { TaskSwimlaneModel } from './task_swimlane_model';
import { TaskSwimlaneRenderer } from './task_swimlane_renderer';

class TaskSwimlaneController extends ProjectTaskKanbanController {
    get modelParams() {
        return { ...super.modelParams, maxGroupByDepth: 2 };
    }
    async onRecordSaved(record, changes) {
        super.onRecordSaved(record);
        if (this.model.root.groupBy?.length === 2 && this.model.root.groupBy.some((spec) => spec.split(':')[0] in (changes || {}))) {
            await this.model.load();
        }
    }
    async createRecord() {
        if (this.model.root.groupBy?.length === 2) return this.props.createRecord();
        return super.createRecord();
    }
}
registry.category('views').add('usl_task_swimlanes', {
    ...projectTaskKanbanView, SearchModel: TaskSwimlaneSearchModel, Model: TaskSwimlaneModel,
    Controller: TaskSwimlaneController, Renderer: TaskSwimlaneRenderer,
});
