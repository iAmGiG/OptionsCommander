"""
Trading agents.

- VoterAgent: MACD+RSI voting
- MultiTimeframeVoter: Multi-timeframe ranked voting (#395)
"""

from .multi_timeframe_voter import (
    MultiTimeframeResult,
    MultiTimeframeVoter,
    TimeframeResult,
    evaluate_multi_timeframe,
    get_multi_timeframe_voter,
)
from .voter_agent import VoterAgent, create_voter_agent

__all__ = [
    "VoterAgent",
    "create_voter_agent",
    "MultiTimeframeVoter",
    "MultiTimeframeResult",
    "TimeframeResult",
    "evaluate_multi_timeframe",
    "get_multi_timeframe_voter",
]
