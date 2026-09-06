import inspect
import json
import logging
import uuid

from odoo import SUPERUSER_ID, _, api, http
from odoo.exceptions import AccessDenied, AccessError
from odoo.http import request
from odoo.models import get_public_method

from ..exceptions import AgentPolicyAccessError
from ..models.agent_policy_tokens import (
    AGENT_OPERATION_SCOPE_CONTEXT_KEY,
    create_agent_operation_scope,
)
from ..models.agent_secrets import (
    AGENT_HIDDEN_API_MODELS,
    is_agent_secret_field,
    sanitize_agent_payload,
)
from odoo.addons.rpc.controllers.json2 import WebJson2Controller

_logger = logging.getLogger(__name__)


class UslAgentJson2Controller(WebJson2Controller):
    _ORM_PAYLOAD_PARAMETERS = {
        "create": "vals_list",
        "write": "vals",
    }

    @http.route()
    def web_json_2_rpc(self, __model__, __method__, ids=(), context=None, **kwargs):
        kwargs = self._normalize_orm_payload_kwargs(
            env=request.env,
            model_name=__model__,
            method_name=__method__,
            kwargs=kwargs,
        )
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
            access = self._check_agent_call(
                agent=agent,
                model_name=__model__,
                method_name=__method__,
                kwargs=kwargs,
            )
            call_context = self._agent_call_context(
                context=context,
                agent=agent,
                model_name=__model__,
                method_name=__method__,
                access=access,
            )
            # HTML collaboration and deferred ORM work also use request.env,
            # not only the recordset context passed to the public method.
            # Keep the authorized scope through precommit without sudoing the
            # request or bypassing any root/non-sudo operation checks.
            request.update_env(context=call_context)
            result = super().web_json_2_rpc(
                __model__,
                __method__,
                ids=ids,
                context=call_context,
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

    @classmethod
    def _normalize_orm_payload_kwargs(
        cls,
        *,
        env,
        model_name,
        method_name,
        kwargs,
    ):
        """Adapt canonical ORM payload names to legacy override signatures."""
        canonical_name = cls._ORM_PAYLOAD_PARAMETERS.get(method_name)
        if canonical_name not in kwargs:
            return kwargs

        try:
            method = get_public_method(env[model_name], method_name)
        except (AccessError, AttributeError, KeyError):
            # Preserve the base JSON-2 controller's canonical error response.
            return kwargs
        parameters = list(inspect.signature(method).parameters.values())
        if parameters and parameters[0].name in {"self", "cls"}:
            parameters = parameters[1:]
        if canonical_name in {parameter.name for parameter in parameters}:
            return kwargs
        if any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters):
            return kwargs

        payload_parameters = [
            parameter
            for parameter in parameters
            if parameter.kind
            in {
                inspect.Parameter.POSITIONAL_ONLY,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            }
        ]
        if len(payload_parameters) != 1:
            return kwargs
        actual_name = payload_parameters[0].name
        if actual_name in kwargs or actual_name in {"ids", "context"}:
            return kwargs

        normalized = dict(kwargs)
        normalized[actual_name] = normalized.pop(canonical_name)
        return normalized

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
        access = agent._api_method_access(model_name, method_name)
        if not access:
            raise AgentPolicyAccessError(
                _(
                    "This Agent has no approved application access for %(model)s.%(method)s.",
                    model=model_name,
                    method=method_name,
                ),
                "agent_read_only_action_denied",
            )
        return access

    @staticmethod
    def _agent_call_context(*, context, agent, model_name, method_name, access):
        call_context = dict(context or {})
        call_context.pop(AGENT_OPERATION_SCOPE_CONTEXT_KEY, None)
        if access in {"collaboration", "write"}:
            call_context[AGENT_OPERATION_SCOPE_CONTEXT_KEY] = (
                create_agent_operation_scope(
                    agent_user_id=agent.user_id.id,
                    root_model=model_name,
                    root_method=method_name,
                    access=access,
                )
            )
        return call_context

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
        # Never write from a second connection while the request transaction
        # is still open. The insert checks foreign keys against rows that this
        # transaction may have locked (the credential usage touch, the Agent
        # authority), so the second connection waits on the first one, which
        # itself waits for this request: a deadlock that PostgreSQL cannot
        # detect and that holds an HTTP worker until its time limit. Record
        # the evidence once the transaction has committed or rolled back.
        registry = request.registry
        cr = request.env.cr

        def record(outcome_override=None):
            recorded = dict(values, outcome=outcome_override or values["outcome"])
            try:
                with registry.cursor() as cursor:
                    env = api.Environment(
                        cursor, SUPERUSER_ID, {"usl_skip_distribution_audit": True},
                    )
                    env["usl.audit.event"]._record_event(recorded)
                    cursor.commit()
            except Exception:
                _logger.exception(
                    "Agent API audit event was not recorded: %s %s",
                    recorded.get("action_name"), recorded.get("request_id"),
                )

        cr.postcommit.add(record)
        # A request that succeeded in the controller can still roll back at
        # commit time. Keep the evidence, but do not call it a success.
        cr.postrollback.add(
            lambda: record("failed" if values["outcome"] == "succeeded" else None),
        )

    @staticmethod
    def _operation_for_method(method_name):
        if method_name in {"read", "search", "search_read", "read_group", "formatted_read_group", "context_get"}:
            return "read"
        if method_name in {"create", "write", "unlink"}:
            return method_name
        return "call"
