import ast
from pathlib import Path
from unittest.mock import patch

from odoo import api
from odoo.tests import BaseCase, TransactionCase, tagged
from werkzeug.exceptions import NotFound

from odoo.addons.usl_access_control.controllers import json2 as json2_controller
from odoo.addons.usl_access_control.controllers.json2 import UslAgentJson2Controller

CANONICAL_PAYLOAD_PARAMETERS = {"create": "vals_list", "write": "vals"}
CUSTOM_ADDONS = Path(__file__).resolve().parents[2]


def _payload_parameter(node):
    """Return the payload argument of an ORM `create`/`write` override."""
    positional = [*node.args.posonlyargs, *node.args.args]
    if len(positional) != 2 or positional[0].arg not in {"self", "cls"}:
        return None
    return positional[1]


@tagged("post_install", "-at_install", "usl_access_control")
class TestOrmPayloadContract(BaseCase):
    """An ORM override must keep the payload keyword the JSON-2 API publishes.

    `/json/2/<model>/<method>` binds request keywords against the live method
    signature, so an override that renames `vals_list` or `vals` renames the
    public API keyword of every model it applies to, for every caller.
    """

    def test_custom_addons_keep_the_canonical_orm_payload_names(self):
        offenders = []
        for path in sorted(CUSTOM_ADDONS.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                canonical = CANONICAL_PAYLOAD_PARAMETERS.get(node.name)
                if not canonical:
                    continue
                payload = _payload_parameter(node)
                if payload is None or payload.arg == canonical:
                    continue
                offenders.append(
                    f"{path.relative_to(CUSTOM_ADDONS)}:{node.lineno}: "
                    f"{node.name}({payload.arg}) must name its payload {canonical!r}",
                )
        self.assertEqual(
            offenders,
            [],
            "ORM overrides must keep the canonical JSON-2 payload keyword:\n"
            + "\n".join(offenders),
        )


@tagged("post_install", "-at_install", "usl_access_control")
class TestOrmPayloadShim(TransactionCase):
    """The keyword shim must serve the callable the controller really calls."""

    def _normalize(self, method, method_name, kwargs):
        with patch.object(json2_controller, "get_public_method", return_value=method):
            return UslAgentJson2Controller._normalize_orm_payload_kwargs(
                env=self.env,
                model_name="res.partner",
                method_name=method_name,
                kwargs=kwargs,
            )

    def test_decorated_create_override_keeps_the_canonical_keyword(self):
        @api.model_create_multi
        def create(self, values_list):
            return values_list

        # `@api.model_create_multi` accepts `vals_list` whatever the override
        # named its own argument, so renaming the payload would raise TypeError.
        self.assertEqual(
            self._normalize(create, "create", {"vals_list": [{"name": "Probe"}]}),
            {"vals_list": [{"name": "Probe"}]},
        )

    def test_renamed_write_override_still_receives_the_payload(self):
        def write(self, values):
            return values

        self.assertEqual(
            self._normalize(write, "write", {"vals": {"name": "Probe"}}),
            {"values": {"name": "Probe"}},
        )


@tagged("post_install", "-at_install", "usl_access_control")
class TestUnknownMethodAnswer(TransactionCase):
    """An unknown method is missing, not withheld."""

    def test_unknown_method_is_reported_as_missing(self):
        with self.assertRaises(NotFound):
            UslAgentJson2Controller._assert_public_method_exists(
                env=self.env,
                model_name="res.partner",
                method_name="update_draft_partner",
            )
        with self.assertRaises(NotFound):
            UslAgentJson2Controller._assert_public_method_exists(
                env=self.env,
                model_name="res.partner.does.not.exist",
                method_name="write",
            )

    def test_an_implemented_method_still_reaches_the_policy(self):
        UslAgentJson2Controller._assert_public_method_exists(
            env=self.env,
            model_name="res.partner",
            method_name="write",
        )
