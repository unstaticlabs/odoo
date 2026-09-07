from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "usl_feedback")
class TestFeedbackViewPriority(TransactionCase):
    """The feedback boards must never become the default views of project.task.

    They are primary views reached exclusively through their own actions. Left at
    the default priority they win ir.ui.view._order ("priority,name,id") against
    the stock views, and every project inherits the read-only feedback board.
    """

    def test_stock_views_stay_default_for_project_task(self):
        View = self.env["ir.ui.view"]
        expected = {
            "kanban": self.env.ref("project.view_task_kanban"),
            "list": self.env.ref("project.view_task_tree2"),
            "form": self.env.ref("project.view_task_form2"),
            "search": self.env.ref("project.view_task_search_form"),
        }
        for view_type, stock_view in expected.items():
            with self.subTest(view_type=view_type):
                self.assertEqual(
                    View.default_view("project.task", view_type),
                    stock_view.id,
                    f"usl_feedback hijacked the default {view_type} view of project.task",
                )

    def test_project_task_action_offers_editable_kanban(self):
        project = self.env["project.project"].create({"name": "Priority regression"})
        action = project.action_view_tasks()
        view_id = dict((mode, view_id) for view_id, mode in action["views"])["kanban"]
        arch = self.env["project.task"].get_views([(view_id, "kanban")])
        kanban = arch["views"]["kanban"]["arch"]
        self.assertNotIn('create="false"', kanban)
        self.assertNotIn('group_create="false"', kanban)

    def test_feedback_actions_bind_their_own_views(self):
        for action_xmlid, suffix in (
            ("usl_feedback.action_feedback_collaborator", "collaborator"),
            ("usl_feedback.action_feedback_maintainer", "maintainer"),
        ):
            action = self.env.ref(action_xmlid)
            bound = {view.view_mode: view.view_id for view in action.view_ids}
            for view_mode in ("kanban", "list", "form"):
                with self.subTest(action=action_xmlid, view_mode=view_mode):
                    self.assertEqual(
                        bound.get(view_mode),
                        self.env.ref(
                            f"usl_feedback.view_feedback_task_{view_mode}_{suffix}"
                        ),
                    )
