"""Unforgeable in-process markers for governed Agent operations."""

from dataclasses import dataclass, field

AGENT_COLLABORATION_TOKEN = object()
AGENT_COLLABORATION_CONTEXT_KEY = "usl_agent_collaboration_token"

AGENT_OPERATION_SCOPE_CONTEXT_KEY = "usl_agent_operation_scope"
_AGENT_OPERATION_SCOPE_MARKER = object()
_MUTATING_AGENT_ACCESS = frozenset({"collaboration", "write"})


@dataclass(frozen=True, slots=True)
class AgentOperationScope:
    """Authority provenance for one server-approved Agent JSON-2 call."""

    agent_user_id: int
    root_model: str
    root_method: str
    access: str
    _marker: object = field(repr=False, compare=False)


def create_agent_operation_scope(*, agent_user_id, root_model, root_method, access):
    if (
        not isinstance(agent_user_id, int)
        or agent_user_id <= 0
        or not isinstance(root_model, str)
        or not root_model
        or not isinstance(root_method, str)
        or not root_method
        or access not in _MUTATING_AGENT_ACCESS
    ):
        message = "Invalid Agent operation scope."
        raise ValueError(message)
    return AgentOperationScope(
        agent_user_id=agent_user_id,
        root_model=root_model,
        root_method=root_method,
        access=access,
        _marker=_AGENT_OPERATION_SCOPE_MARKER,
    )


def get_agent_operation_scope(context, *, agent_user_id):
    scope = context.get(AGENT_OPERATION_SCOPE_CONTEXT_KEY)
    if (
        not isinstance(scope, AgentOperationScope)
        or scope._marker is not _AGENT_OPERATION_SCOPE_MARKER
        or scope.agent_user_id != agent_user_id
        or scope.access not in _MUTATING_AGENT_ACCESS
    ):
        return None
    return scope


def has_agent_collaboration_token(context):
    return context.get(AGENT_COLLABORATION_CONTEXT_KEY) is AGENT_COLLABORATION_TOKEN
