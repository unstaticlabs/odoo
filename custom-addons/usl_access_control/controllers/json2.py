import json
import uuid

from odoo import SUPERUSER_ID, _, api, http
from odoo.exceptions import AccessDenied, AccessError
from odoo.http import request
from odoo.addons.rpc.controllers.json2 import WebJson2Controller

from ..exceptions import AgentPolicyAccessError
from ..models.action_policy import (
    ActionPolicyConfigurationError,
    load_agent_readonly_policy,
)
from ..models.agent_secrets import (
    AGENT_HIDDEN_API_MODELS,
    is_agent_secret_field,
    sanitize_agent_payload,
)

class UslAgentJson2Controller(WebJson2Controller):
    @http.route()
    def web_json_2_rpc(self, __model__, __method__, ids=(), context=None, **kwargs):
        agent = request.env["usl.agent"].sudo().search(
            [("user_id", "=", request.env.uid)],
            limit=1,
        )
        if not agent:
            return super().web_json_2_rpc(
                __model__,
                __method__,
                ids=ids,
                context=context or {},
                **kwargs,
            )
        identity_probe = __model__ == "usl.agent" and __method__ == "current_identity"
        request_id = request.httprequest.headers.get("X-Request-Id") or str(uuid.uuid4())
        correlation_id = (
            request.httprequest.headers.get("X-Correlation-Id")
            or (context or {}).get("usl_correlation_id")
            or request_id
        )
        outcome = "succeeded"
        try:
            self._check_agent_call(
                agent=agent,
                model_name=__model__,
                method_name=__method__,
                kwargs=kwargs,
            )
            result = super().web_json_2_rpc(
                __model__,
                __method__,
                ids=ids,
                context=context or {},
                **kwargs,
            )
            return (
                result
                if identity_probe
                else sanitize_agent_payload(result, model_name=__model__)
            )
        except Exception as error:
            outcome = "denied" if isinstance(error, (AccessDenied, AccessError)) else "failed"
            raise
        finally:
            if not identity_probe:
                self._record_agent_api_call(
                    agent=agent,
                    model_name=__model__,
                    method_name=__method__,
                    record_ids=ids,
                    outcome=outcome,
                    request_id=request_id,
                    correlation_id=correlation_id,
                )

    @staticmethod
    def _check_agent_call(*, agent, model_name, method_name, kwargs):
        if model_name in AGENT_HIDDEN_API_MODELS:
            raise AgentPolicyAccessError(
                _("Agent credentials and secrets are not exposed through the API."),
                "agent_read_only_action_denied",
            )
        requested_fields = []
        for parameter_name in ("fields", "fields_to_export"):
            value = kwargs.get(parameter_name)
            if isinstance(value, (list, tuple)):
                requested_fields.extend(value)
        if any(
            is_agent_secret_field(field_name, model_name=model_name)
            for field_name in requested_fields
        ):
            raise AgentPolicyAccessError(
                _("Secret fields are not available to Agents."),
                "agent_read_only_action_denied",
            )
        try:
            access = load_agent_readonly_policy().access_for(model_name, method_name)
        except ActionPolicyConfigurationError as error:
            raise AgentPolicyAccessError(
                _(
                    "The Agent read-only policy is invalid. Contact an administrator.",
                ),
                "agent_read_only_action_denied",
            ) from error
        if access not in {"read_only", "collaboration"}:
            operation = method_name if method_name in {"create", "write", "unlink"} else "write"
            if access == "write" and agent._allows_model_operation(model_name, operation):
                return
            raise AgentPolicyAccessError(
                _(
                    "This Agent has no approved application access for %(model)s.%(method)s.",
                    model=model_name,
                    method=method_name,
                ),
                "agent_read_only_action_denied",
            )

    def _record_agent_api_call(
        self,
        *,
        agent,
        model_name,
        method_name,
        record_ids,
        outcome,
        request_id,
        correlation_id,
    ):
        values = {
            "actor_id": agent.user_id.id,
            "actor_is_agent": True,
            "agent_id": agent.id,
            "owner_id": agent.owner_id.id,
            "credential_id": getattr(request, "usl_agent_credential_id", None),
            "company_id": request.env.company.id,
            "event_type": "api_call",
            "outcome": outcome,
            "model_name": model_name,
            "record_ids": json.dumps(list(record_ids or ())[:100]),
            "record_count": len(record_ids or ()),
            "operation": self._operation_for_method(method_name),
            "action_name": f"{model_name}.{method_name}",
            "origin": "json2",
            "correlation_id": str(correlation_id)[:128],
            "request_id": str(request_id)[:128],
            "remote_address": (request.httprequest.remote_addr or "")[:128],
            "user_agent": (request.httprequest.user_agent.string or "")[:512],
        }
        with request.registry.cursor() as cursor:
            env = api.Environment(cursor, SUPERUSER_ID, {"usl_skip_distribution_audit": True})
            env["usl.audit.event"]._record_event(values)
            cursor.commit()

    @staticmethod
    def _operation_for_method(method_name):
        if method_name in {"read", "search", "search_read", "read_group", "formatted_read_group", "context_get"}:
            return "read"
        if method_name in {"create", "write", "unlink"}:
            return method_name
        return "call"
