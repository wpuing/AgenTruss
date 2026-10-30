"""AgentScope 2.x adapter package (optional dependency).

Importing this package does not import ``agentscope`` until
``AgentScopeProvider`` runs and availability is confirmed.
"""

from app.runtime.agentscope_ext.availability import agentscope_available, require_agentscope
from app.runtime.agentscope_ext.provider import AgentScopeProvider

__all__ = [
    "AgentScopeProvider",
    "agentscope_available",
    "require_agentscope",
]
