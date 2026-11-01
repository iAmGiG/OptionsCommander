"""
Input parsers for converting user input into structured TradeRequest.

Available parsers:
- LLMParser: Natural language parsing with an LLM tool call (Microsoft Agent Framework,
  #606).
- RegexParser: Simple pattern matching (future fallback)
- StructuredParser: Direct input from GUI (future)

Issue #406: Consolidated LLM usage to a single integration path.
"""

from .llm_parser import LLMParser

__all__ = [
    "LLMParser",
]
