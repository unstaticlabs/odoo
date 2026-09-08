from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from operations import docs_reference

PAYLOAD = {
    "schema": "usl-docs-reference/v1",
    "lang": "en_US",
    "models": [
        {
            "model": "usl.expense.batch",
            "description": "Expense Batch",
            "explanation": "Groups related expenses.",
            "module": "usl_expense_batch",
            "owned": True,
            "transient": False,
            "fields": [
                {"name": "name", "label": "Name", "type": "char", "required": True, "readonly": False, "stored": True,
                 "relation": "", "selection": [], "help": "What the trip or event was.", "module": "usl_expense_batch"},
                {"name": "state", "label": "Status", "type": "selection", "required": False, "readonly": True, "stored": True,
                 "relation": "", "selection": [{"value": "draft", "label": "Draft"}, {"value": "done", "label": "Done"}],
                 "help": "", "module": "usl_expense_batch"},
            ],
        },
        {
            "model": "hr.expense",
            "description": "Expense",
            "explanation": "",
            "module": "",
            "owned": False,
            "transient": False,
            "fields": [
                {"name": "expense_batch_id", "label": "Batch", "type": "many2one", "required": False, "readonly": False,
                 "stored": True, "relation": "usl.expense.batch", "selection": [], "help": "", "module": "usl_expense_batch"},
            ],
        },
    ],
    "states": [
        {"model": "usl.expense.batch", "description": "Expense Batch", "field": "state", "label": "Status", "help": "",
         "values": [{"value": "draft", "label": "Draft"}, {"value": "done", "label": "Done"}]},
    ],
    "groups": [
        {"name": "Manager", "full_name": "Expenses / Manager", "category": "Human Resources / Expenses",
         "module": "usl_expense_batch", "comment": "Approves batches.", "implies": ["Expenses / User"]},
    ],
    "menus": [
        {"path": "Expenses/Expense Batches", "module": "usl_expense_batch", "action": "Expense Batches",
         "action_type": "ir.actions.act_window", "groups": [], "sequence": 10},
    ],
    "settings": [
        {"name": "usl_batch_default", "label": "Default Batch", "type": "boolean", "required": False, "readonly": False,
         "stored": True, "relation": "", "selection": [], "help": "Create a Batch by default.", "module": "usl_expense_batch"},
    ],
    "coverage": {"usl_expense_batch": {"documented": 2, "total": 4}},
}


class DocsReferenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.reference = Path(self.tmp.name) / "reference.json"
        self.reference.write_text(json.dumps(PAYLOAD), encoding="utf-8")
        self.docs = Path(self.tmp.name) / "users"
        self.docs.mkdir()

    def test_every_page_renders_with_front_matter_and_the_facts(self):
        pages = docs_reference.rendered_pages(PAYLOAD)
        names = sorted(path.as_posix() for path in pages)
        self.assertEqual(names, [
            "reference/generated/fields-usl-expense-batch.md",
            "reference/generated/help-coverage.md",
            "reference/generated/menus.md",
            "reference/generated/roles.md",
            "reference/generated/settings.md",
            "reference/generated/states.md",
        ])
        fields = pages[Path("reference/generated/fields-usl-expense-batch.md")]
        self.assertTrue(fields.startswith("---\ntitle: \"Fields: usl_expense_batch\"\ntype: reference\n"))
        self.assertIn("generated: true\n", fields)
        self.assertIn("## Expense Batch\n\n`usl.expense.batch`, defined by this module.\n\nGroups related expenses.", fields)
        self.assertIn("| **Name** (`name`) | char | yes | What the trip or event was. |", fields)
        self.assertIn("Values: **Draft**, **Done**.", fields)
        self.assertIn("## Expense\n\n`hr.expense`, extended by this module (defined by `Odoo`).", fields)
        self.assertIn("many2one → `usl.expense.batch`", fields)
        self.assertIn("- **Draft** (`draft`)", pages[Path("reference/generated/states.md")])
        self.assertIn("| **Manager** (`Expenses / Manager`) | Human Resources / Expenses | `Expenses / User` | Approves batches. |", pages[Path("reference/generated/roles.md")])
        self.assertIn("| **Expenses/Expense Batches** | Expense Batches | everyone with the application |", pages[Path("reference/generated/menus.md")])
        self.assertIn("| **Default Batch** (`usl_batch_default`) | boolean | Create a Batch by default. | `usl_expense_batch` |", pages[Path("reference/generated/settings.md")])
        self.assertIn("| `usl_expense_batch` | 2 | 4 | 50 % |", pages[Path("reference/generated/help-coverage.md")])

    def test_render_is_deterministic_and_check_reports_drift(self):
        written = docs_reference.render_all(self.reference, self.docs)
        self.assertEqual(len(written), 6)
        self.assertEqual(docs_reference.render_all(self.reference, self.docs), [])
        self.assertEqual(docs_reference.check_all(self.reference, self.docs), [])
        (self.docs / "reference" / "generated" / "menus.md").write_text("stale", encoding="utf-8")
        findings = docs_reference.check_all(self.reference, self.docs)
        self.assertEqual(len(findings), 1)
        self.assertIn("differs from the registry", findings[0][1])

    def test_the_wrong_schema_is_refused(self):
        self.reference.write_text(json.dumps({"schema": "other"}), encoding="utf-8")
        with self.assertRaises(docs_reference.DocsReferenceError):
            docs_reference.load(self.reference)


if __name__ == "__main__":
    unittest.main()
