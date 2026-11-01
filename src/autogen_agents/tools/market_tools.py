"""
Clean Tools Configuration for RH2MAS
Active tools: unified market data (Alpaca primary, Alpha Vantage fallback; Polygon removed, #607)

Note: News/sentiment tools removed - deprecated V0-V4 sentiment analysis.
"""

# Standard library imports
import json
import logging
import os

# Load tool descriptions from YAML
import sys
from typing import Any

# Third-party imports
import pandas as pd
from agent_framework import FunctionTool

from src.market_data.market_context_tool import market_context_tool

# Project imports - only tools actually used
from src.market_data.unified_market_tool import fetch_unified_market_data
from src.market_data.vxx_volatility_tool import fetch_vxx_volatility_data

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from utils.agent_utils import load_agent_config

logger = logging.getLogger(__name__)

##################################
# Agent Types
##################################

SENTIMENT_AGENT = "sentiment"
TECH_AGENT = "tech"
STRATEGY_AGENT = "strategy"
ALL_AGENTS = [SENTIMENT_AGENT, TECH_AGENT, STRATEGY_AGENT]

##################################
# Load Tool Descriptions from YAML
##################################


def _get_tool_description(tool_key: str, fallback: str) -> str:
    """
    Get tool description from YAML config with fallback.

    Args:
        tool_key: The key for the tool in agent_prompts.yaml
        fallback: Default description if YAML not available

    Returns:
        Tool description string
    """
    try:
        tools_config = load_agent_config("tools")
        return tools_config.get(tool_key, {}).get("description", fallback)
    except Exception:
        return fallback


# Note: Individual Alpha Vantage and hierarchical market data tools removed
# All market data access now handled by unified_market_tool for consistency

# Note: Polygon.io was removed as a data source in Oct 2026 (#607)


def market_data_result(result: Any) -> str:
    """
    A market data result as the model sees it: a DataFrame becomes JSON records with
    the date index as a column and dates as ISO strings.

    Agent Framework's default serializer fails on a date-indexed DataFrame (its
    Timestamp keys are not JSON keys).
    """
    if not isinstance(result, pd.DataFrame):
        return json.dumps(result, default=str)
    if result.empty:
        return json.dumps(
            {
                "data": [],
                "message": "No data returned: the symbol or dates may have none, or the "
                "data sources may be failing. Don't read this as the market being flat.",
            }
        )
    frame = result if isinstance(result.index, pd.RangeIndex) else result.reset_index()
    return frame.to_json(orient="records", date_format="iso")


# Unified market data tool using cache adapter
unified_market_tool = FunctionTool(
    func=fetch_unified_market_data,
    name="fetch_unified_market_data",
    description=_get_tool_description(
        "unified_market_data",
        "Fetch market data using unified cache system. Checks the cache first, then Alpaca (primary), with Alpha Vantage as the fallback.",
    ),
    result_parser=market_data_result,
)
unified_market_tool.agent_types = [TECH_AGENT]

##################################
# VXX Volatility Tool
##################################

vxx_volatility_tool = FunctionTool(
    func=fetch_vxx_volatility_data,
    name="fetch_vxx_volatility_data",
    description=_get_tool_description(
        "vxx_volatility_data",
        "Fetch VXX volatility data for market volatility analysis. Returns VXX-based volatility metrics.",
    ),
)
vxx_volatility_tool.agent_types = [TECH_AGENT]

##################################
# Tool Collections by Agent Type
##################################

# TECH_AGENT tools - Unified market data with cache adapter routing
_tech_tools_raw = [
    unified_market_tool,
    vxx_volatility_tool,
    market_context_tool,
]
TECH_TOOLS = [tool for tool in _tech_tools_raw if tool is not None]

# SENTIMENT_TOOLS - Deprecated (V0-V4 sentiment removed)
SENTIMENT_TOOLS = []

# STRATEGY_AGENT tools - Strategy aggregates outputs from other agents
STRATEGY_TOOLS = []

# All tools combined (filter out None values from conditional imports)
ALL_TOOLS = list(
    {tool for tool in (SENTIMENT_TOOLS + TECH_TOOLS + STRATEGY_TOOLS) if tool is not None}
)

# Tool dispatcher dictionary for efficient lookup by name
ALL_TOOLS_DICT = {tool.name: tool for tool in ALL_TOOLS if tool is not None}

##################################
# Helper function to get tools for a specific agent type
##################################


def get_tools_for_agent(agent_type):
    """
    Get the list of tools that should be used by a specific agent type.

    Args:
        agent_type: Type of agent (e.g., 'sentiment', 'tech', 'strategy')

    Returns:
        List of FunctionTool objects appropriate for the agent type
    """
    if agent_type == SENTIMENT_AGENT:
        return SENTIMENT_TOOLS
    elif agent_type == TECH_AGENT:
        return TECH_TOOLS
    elif agent_type == STRATEGY_AGENT:
        return STRATEGY_TOOLS
    else:
        # Return all tools if agent type is unknown
        return ALL_TOOLS
