"""Unforgeable in-process markers for governed Agent collaboration calls."""

AGENT_COLLABORATION_TOKEN = object()
AGENT_COLLABORATION_CONTEXT_KEY = "usl_agent_collaboration_token"


def has_agent_collaboration_token(context):
    return context.get(AGENT_COLLABORATION_CONTEXT_KEY) is AGENT_COLLABORATION_TOKEN
