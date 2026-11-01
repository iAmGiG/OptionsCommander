"""
Agents package, on Microsoft Agent Framework (#606; the package name is from its AutoGen days).

- core/: BaseAgent
- agents/: VoterAgent, MultiTimeframeVoter, and the scanner watchlist config
- orchestration/: AgentBus
- tools/: market data tools

The orchestrators, agent factory, workflow, and the scanner, risk and executor agents were
unreachable and were removed (#331).
"""

from .agents import VoterAgent, create_voter_agent
from .core import BaseAgent
from .orchestration import (
    AgentBus,
    AgentMessage,
    EventType,
    Subscription,
    create_message,
    get_agent_bus,
    publish_result,
    publish_signal,
    publish_trade_executed,
)

__all__ = [
    "BaseAgent",
    "VoterAgent",
    "create_voter_agent",
    "EventType",
    "AgentMessage",
    "Subscription",
    "AgentBus",
    "get_agent_bus",
    "create_message",
    "publish_result",
    "publish_signal",
    "publish_trade_executed",
]
