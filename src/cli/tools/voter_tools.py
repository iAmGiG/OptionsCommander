"""
Voter Agent CLI Tools - FunctionTool wrappers for MACD+RSI voting strategy.

Issue #488: /voter command group for visibility and configuration.

This module wraps VoterAgent functionality as FunctionTool instances
for integration with the AutoGen agent architecture.

Includes:
- Configuration display (show_voter_config, explain_* functions)
- Ranked voting management (list_voters, apply_preset, promote/demote)

Original Implementation: src/trading/instruments/voting_signal.py
Pattern: Pure function wrappers → FunctionTool → Registry
"""

from typing import Any, Dict, Literal

from agent_framework import FunctionTool

from config_defaults.trading_config import TradingConfig

from src.cli.commands.voter_commands import get_voter_commands

# ============================================================================
# Pure Function Wrappers
# ============================================================================


def show_voter_config() -> str:
    """
    Display current VoterAgent configuration.

    Shows all MACD and RSI parameters used by the voting strategy,
    along with voting thresholds and backtested performance metrics.

    Returns:
        Formatted string with configuration details

    Example:
        >>> show_voter_config()
        'Voter Configuration
        MACD: 13/34/8 (Fibonacci)
        RSI: 14 [30/70]
        ...
        Backtest of the Live Rules (2009 to 2026-10-09, 5 bps a side)
          Sharpe Ratio:  0.69
          ...'
    """
    config = TradingConfig()
    macd = config.get_macd_config()
    rsi = config.get_rsi_config()

    output = "📊 VoterAgent Configuration\n"
    output += "=" * 50 + "\n"

    # MACD settings
    output += "MACD Parameters (Fibonacci)\n"
    output += f"  Fast Period:   {macd.fast}\n"
    output += f"  Slow Period:   {macd.slow}\n"
    output += f"  Signal Period: {macd.signal}\n"
    output += "-" * 50 + "\n"

    # RSI settings
    output += "RSI Parameters\n"
    output += f"  Period:     {rsi.period}\n"
    output += f"  Oversold:   {rsi.oversold}\n"
    output += f"  Overbought: {rsi.overbought}\n"
    output += "-" * 50 + "\n"

    # Voting thresholds (hardcoded validated values)
    output += "Voting Thresholds\n"
    output += "  MACD Threshold:    0.1\n"
    output += "  Consensus Boost:   0.15\n"
    output += "  Weak Signal Boost: 0.10\n"
    output += "  Min Data Points:   42\n"
    output += "-" * 50 + "\n"

    # Backtest of the live rules (#574; docs/08_research/03_strategy_research/live_rules_baseline.md)
    output += "Backtest of the Live Rules (2009 to 2026-10-09, 5 bps a side)\n"
    output += "  Sharpe Ratio:  0.69\n"
    output += "  Alpha vs buy-and-hold of the same symbols: +0.11%/yr (t = 0.19), no edge\n"
    output += "  Win Rate:      44.2%\n"
    output += "  Max Drawdown:  -6.9%\n"

    return output


def explain_voting_logic() -> str:
    """
    Explain how the MACD+RSI voting strategy works.

    Describes the consensus logic, signal types, and position sizing
    based on the voting outcome.

    Returns:
        Formatted explanation of the voting system

    Example:
        >>> explain_voting_logic()
        'Voting Logic
        STRONG: Both agree → Full position (1.0)
        WEAK: One signals → Half position (0.5)
        HOLD: Conflict/Neutral → No position'
    """
    output = "🗳️ MACD+RSI Voting Logic\n"
    output += "=" * 50 + "\n\n"

    output += "How It Works:\n"
    output += "-" * 50 + "\n"
    output += "1. MACD generates BUY/SELL/HOLD based on histogram\n"
    output += "2. RSI generates BUY/SELL/HOLD based on oversold/overbought\n"
    output += "3. Votes are combined for consensus decision\n\n"

    output += "Signal Types:\n"
    output += "-" * 50 + "\n"
    output += "STRONG (Full Position 1.0x)\n"
    output += "  Both MACD and RSI agree on direction\n"
    output += "  Confidence: 0.75-0.85\n\n"

    output += "WEAK (Half Position 0.5x)\n"
    output += "  One indicator signals, other is neutral\n"
    output += "  Confidence: 0.60-0.70\n\n"

    output += "CONFLICT (No Position)\n"
    output += "  MACD and RSI disagree\n"
    output += "  Action: HOLD\n\n"

    output += "NEUTRAL (No Position)\n"
    output += "  Both indicators are neutral\n"
    output += "  Action: HOLD\n"

    return output


def explain_macd_params() -> str:
    """
    Explain the MACD parameters and why Fibonacci values are used.

    Provides detailed explanation of each MACD parameter and the
    reasoning behind the 13/34/8 Fibonacci configuration.

    Returns:
        Formatted explanation of MACD parameters

    Example:
        >>> explain_macd_params()
        'MACD Parameters
        Fast (13): Short-term EMA
        Slow (34): Long-term EMA
        Signal (8): Signal line smoothing'
    """
    output = "📈 MACD Parameters Explained\n"
    output += "=" * 50 + "\n\n"

    output += "Current: 13/34/8 (Fibonacci Sequence)\n"
    output += "-" * 50 + "\n\n"

    output += "Fast Period (13)\n"
    output += "  Short-term exponential moving average\n"
    output += "  Reacts quickly to price changes\n"
    output += "  Fibonacci: 13 is in the sequence (1,1,2,3,5,8,13,21,34...)\n\n"

    output += "Slow Period (34)\n"
    output += "  Long-term exponential moving average\n"
    output += "  Provides trend context\n"
    output += "  Fibonacci: 34 is in the sequence\n\n"

    output += "Signal Period (8)\n"
    output += "  Smoothing for the MACD line\n"
    output += "  Crossovers generate signals\n"
    output += "  Fibonacci: 8 is in the sequence\n\n"

    output += "Why Fibonacci?\n"
    output += "-" * 50 + "\n"
    output += "  Chosen from tests on 7 tech stocks (2024-2025)\n"
    output += "  Beat 12/26/9 there; no MACD setting was robust out-of-sample (#518)\n"
    output += "  Better alignment with market cycles\n"

    return output


def explain_rsi_params() -> str:
    """
    Explain the RSI parameters and threshold meanings.

    Provides detailed explanation of RSI calculation and
    the significance of oversold/overbought thresholds.

    Returns:
        Formatted explanation of RSI parameters

    Example:
        >>> explain_rsi_params()
        'RSI Parameters
        Period (14): Lookback window
        Oversold (30): Buy signal threshold
        Overbought (70): Sell signal threshold'
    """
    output = "📉 RSI Parameters Explained\n"
    output += "=" * 50 + "\n\n"

    output += "Current: 14 period, 30/70 thresholds\n"
    output += "-" * 50 + "\n\n"

    output += "Period (14)\n"
    output += "  Number of bars for RSI calculation\n"
    output += "  Industry standard, well-tested\n"
    output += "  Balances sensitivity and noise\n\n"

    output += "Oversold Threshold (30)\n"
    output += "  RSI below 30 = oversold condition\n"
    output += "  Generates BUY signal\n"
    output += "  Indicates potential price reversal up\n\n"

    output += "Overbought Threshold (70)\n"
    output += "  RSI above 70 = overbought condition\n"
    output += "  Generates SELL signal\n"
    output += "  Indicates potential price reversal down\n\n"

    output += "RSI Ranges:\n"
    output += "-" * 50 + "\n"
    output += "  0-30:  Oversold → BUY signal\n"
    output += "  30-70: Neutral → HOLD\n"
    output += "  70-100: Overbought → SELL signal\n"

    return output


def get_voter_parameters() -> Dict[str, Any]:
    """
    Get voter parameters as structured data for agents.

    Provides voting configuration in a structured format suitable
    for programmatic consumption by other agents.

    Returns:
        Dictionary with all voter parameters

    Example:
        >>> get_voter_parameters()
        {'macd': {'fast': 13, 'slow': 34, 'signal': 8}, ...}
    """
    config = TradingConfig()
    macd = config.get_macd_config()
    rsi = config.get_rsi_config()

    return {
        "macd": {
            "fast": macd.fast,
            "slow": macd.slow,
            "signal": macd.signal,
        },
        "rsi": {
            "period": rsi.period,
            "oversold": rsi.oversold,
            "overbought": rsi.overbought,
        },
        "thresholds": {
            "macd_threshold": 0.1,
            "consensus_boost": 0.15,
            "weak_signal_boost": 0.10,
            "min_data_points": 42,
        },
        "performance": {  # backtest of the live rules (#574), 5 bps a side
            "sharpe_ratio": 0.69,
            "sharpe_ratio_2020_on": 0.71,
            "sharpe_ci95_2020_on": [-0.07, 1.50],
            "alpha_vs_buy_and_hold": 0.0011,  # per year, t = 0.19
            "win_rate": 0.442,
            "max_drawdown": -0.069,
            "backtest_period": "2009-01-02 to 2026-10-09",
            "edge": "none over buy-and-hold of the same symbols",
        },
    }


def compare_with_traditional() -> str:
    """
    Compare Fibonacci MACD (13/34/8) with traditional (12/26/9).

    Shows side-by-side comparison of parameter sets and their
    performance characteristics.

    Returns:
        Formatted comparison table

    Example:
        >>> compare_with_traditional()
        'MACD Parameter Comparison
        | Param    | Fibonacci | Traditional |
        | Fast     |    13     |     12      |'
    """
    output = "📊 MACD Parameter Comparison\n"
    output += "=" * 50 + "\n\n"

    output += f"{'Parameter':<15} {'Fibonacci':>12} {'Traditional':>12}\n"
    output += "-" * 50 + "\n"
    output += f"{'Fast Period':<15} {'13':>12} {'12':>12}\n"
    output += f"{'Slow Period':<15} {'34':>12} {'26':>12}\n"
    output += f"{'Signal Period':<15} {'8':>12} {'9':>12}\n"
    output += "-" * 50 + "\n\n"

    output += "Fibonacci Advantages:\n"
    output += "  Better trend detection in tech stocks\n"
    output += "  Fewer false signals in choppy markets\n"
    output += "  Used by the live CLI (backtest: no edge over buy-and-hold, #574)\n\n"

    output += "Traditional Use Cases:\n"
    output += "  Broader market indices\n"
    output += "  More responsive to short-term moves\n"
    output += "  Industry standard for backtesting\n"

    return output


def show_signal_interpretation(signal_type: str = "STRONG") -> str:
    """
    Show how to interpret a specific signal type.

    Provides guidance on position sizing, confidence levels,
    and risk management for each signal type.

    Args:
        signal_type: Signal to explain ('STRONG', 'WEAK', 'CONFLICT', 'NEUTRAL')

    Returns:
        Formatted explanation of the signal type

    Example:
        >>> show_signal_interpretation('STRONG')
        'STRONG Signal
        Position: Full (1.0x)
        Confidence: 0.75-0.85
        Action: Enter with standard risk'
    """
    signal_type = signal_type.upper()

    interpretations = {
        "STRONG": {
            "emoji": "💪",
            "position": "Full (1.0x)",
            "confidence": "0.75-0.85",
            "description": "Both MACD and RSI agree on direction",
            "action": "Enter with standard position size",
            "risk_note": "Use normal stop loss (per mode settings)",
        },
        "WEAK": {
            "emoji": "🤏",
            "position": "Half (0.5x)",
            "confidence": "0.60-0.70",
            "description": "One indicator signals, other is neutral",
            "action": "Enter with reduced position size",
            "risk_note": "Consider tighter stop or wait for confirmation",
        },
        "CONFLICT": {
            "emoji": "⚔️",
            "position": "None (0.0x)",
            "confidence": "0.20",
            "description": "MACD and RSI disagree on direction",
            "action": "Do not enter - signals conflict",
            "risk_note": "Wait for agreement before trading",
        },
        "NEUTRAL": {
            "emoji": "😐",
            "position": "None (0.0x)",
            "confidence": "0.20",
            "description": "Both indicators are neutral",
            "action": "Do not enter - no clear signal",
            "risk_note": "Market may be consolidating",
        },
    }

    if signal_type not in interpretations:
        return f"❌ Unknown signal type: {signal_type}. Valid: STRONG, WEAK, CONFLICT, NEUTRAL"

    info = interpretations[signal_type]

    output = f"{info['emoji']} {signal_type} Signal Interpretation\n"
    output += "=" * 50 + "\n"
    output += f"  Position Size: {info['position']}\n"
    output += f"  Confidence:    {info['confidence']}\n"
    output += "-" * 50 + "\n"
    output += f"  {info['description']}\n\n"
    output += f"  Action: {info['action']}\n"
    output += f"  Risk: {info['risk_note']}\n"

    return output


# ============================================================================
# Ranked Voting Management Functions (Issue #488)
# ============================================================================


def list_voters(verbose: bool = False) -> str:
    """
    List all voters with their rankings and roles.

    Shows active voters (participate in decisions) and review voters
    (shown for reference but don't vote).

    Args:
        verbose: Show detailed parameters for each voter

    Returns:
        Formatted list of voters with rankings

    Example:
        >>> list_voters()
        'Active Voters:
           #1 MACD
           #2 RSI
         Review Voters:
           (none)'
    """
    return get_voter_commands().list_voters(verbose=verbose)


def show_voting_info() -> str:
    """
    Show current voting configuration settings.

    Displays consensus mode, confidence adjustments, and position sizing
    rules for the ranked voting system.

    Returns:
        Formatted voting configuration

    Example:
        >>> show_voting_info()
        'Voting Configuration
         Mode: unanimous
         Active Voters: 2'
    """
    return get_voter_commands().show_info()


def list_voter_presets() -> str:
    """
    List available voter presets.

    Shows all configured presets with descriptions. Presets define
    different voter ranking configurations for various trading styles.

    Returns:
        Formatted list of presets

    Example:
        >>> list_voter_presets()
        'Available Presets:
           default: MACD+RSI voting
           macd_primary: MACD-focused with RSI confirmation'
    """
    return get_voter_commands().list_presets()


def apply_voter_preset(preset_name: Literal["default", "macd_primary", "rsi_primary"]) -> str:
    """
    Apply a voter preset configuration.

    Changes the voter ranking to match the specified preset.
    Changes persist to the database across sessions.

    Args:
        preset_name: Name of preset to apply

    Returns:
        Status message

    Example:
        >>> apply_voter_preset('macd_primary')
        '✅ Applied preset macd_primary'
    """
    return get_voter_commands().apply_preset(preset_name)


def promote_voter(name: Literal["MACD", "RSI"]) -> str:
    """
    Promote a voter one rank higher.

    Moves the specified voter up in the ranking. If the voter
    is already at rank 1, no change is made.

    Args:
        name: Voter name (MACD or RSI)

    Returns:
        Status message

    Example:
        >>> promote_voter('RSI')
        '✅ Promoted RSI to rank #1'
    """
    return get_voter_commands().promote_voter(name)


def demote_voter(name: Literal["MACD", "RSI"]) -> str:
    """
    Demote a voter one rank lower.

    Moves the specified voter down in the ranking. If the voter
    is already at the bottom rank, no change is made.

    Args:
        name: Voter name (MACD or RSI)

    Returns:
        Status message

    Example:
        >>> demote_voter('MACD')
        '✅ Demoted MACD to rank #2'
    """
    return get_voter_commands().demote_voter(name)


def set_voter_role(name: Literal["MACD", "RSI"], role: Literal["active", "review"]) -> str:
    """
    Change a voter's role (active or review).

    Active voters participate in trading decisions.
    Review voters are shown for reference but don't vote.

    Args:
        name: Voter name (MACD or RSI)
        role: New role (active or review)

    Returns:
        Status message

    Example:
        >>> set_voter_role('RSI', 'review')
        '✅ Set RSI to review role'
    """
    return get_voter_commands().set_voter_role(name, role)


# ============================================================================
# FunctionTool Definitions
# ============================================================================

show_voter_config_tool = FunctionTool(
    func=show_voter_config,
    name="show_voter_config",
    description=(
        "Display current VoterAgent configuration including MACD parameters, "
        "RSI settings, voting thresholds, and backtested performance metrics."
    ),
)

explain_voting_logic_tool = FunctionTool(
    func=explain_voting_logic,
    name="explain_voting_logic",
    description=(
        "Explain how the MACD+RSI voting strategy works, including signal types "
        "(STRONG, WEAK, CONFLICT, NEUTRAL) and position sizing rules."
    ),
)

explain_macd_params_tool = FunctionTool(
    func=explain_macd_params,
    name="explain_macd_params",
    description=(
        "Explain MACD parameters and why Fibonacci values (13/34/8) "
        "are used instead of traditional (12/26/9)."
    ),
)

explain_rsi_params_tool = FunctionTool(
    func=explain_rsi_params,
    name="explain_rsi_params",
    description=(
        "Explain RSI parameters including period, oversold/overbought thresholds, "
        "and how they generate trading signals."
    ),
)

get_voter_parameters_tool = FunctionTool(
    func=get_voter_parameters,
    name="get_voter_parameters",
    description=(
        "Get VoterAgent parameters as structured data for programmatic use "
        "by other agents. Returns dict with MACD, RSI, and threshold settings."
    ),
)

compare_with_traditional_tool = FunctionTool(
    func=compare_with_traditional,
    name="compare_with_traditional",
    description=(
        "Compare Fibonacci MACD parameters (13/34/8) with traditional (12/26/9). "
        "Shows parameter differences and use case recommendations."
    ),
)

show_signal_interpretation_tool = FunctionTool(
    func=show_signal_interpretation,
    name="show_signal_interpretation",
    description=(
        "Show how to interpret a specific voter signal type (STRONG, WEAK, "
        "CONFLICT, NEUTRAL) with position sizing and risk guidance."
    ),
)

# Ranked Voting Management Tools (Issue #488)
list_voters_tool = FunctionTool(
    func=list_voters,
    name="list_voters",
    description=(
        "List all voters with their rankings and roles. Shows active voters "
        "(participate in decisions) and review voters (shown but don't vote)."
    ),
)

show_voting_info_tool = FunctionTool(
    func=show_voting_info,
    name="show_voting_info",
    description=(
        "Show current voting configuration including consensus mode, "
        "confidence adjustments, and position sizing rules."
    ),
)

list_voter_presets_tool = FunctionTool(
    func=list_voter_presets,
    name="list_voter_presets",
    description=(
        "List available voter presets with descriptions. Presets define "
        "different voter ranking configurations for various trading styles."
    ),
)

apply_voter_preset_tool = FunctionTool(
    func=apply_voter_preset,
    name="apply_voter_preset",
    description=(
        "Apply a voter preset configuration. Available presets: default, "
        "macd_primary, rsi_primary. Changes persist across sessions."
    ),
)

promote_voter_tool = FunctionTool(
    func=promote_voter,
    name="promote_voter",
    description=(
        "Promote a voter one rank higher in the ranking. "
        "Higher-ranked voters have more influence on decisions."
    ),
)

demote_voter_tool = FunctionTool(
    func=demote_voter,
    name="demote_voter",
    description=(
        "Demote a voter one rank lower in the ranking. "
        "Lower-ranked voters have less influence on decisions."
    ),
)

set_voter_role_tool = FunctionTool(
    func=set_voter_role,
    name="set_voter_role",
    description=(
        "Change a voter's role to 'active' (participates in decisions) "
        "or 'review' (shown for reference but doesn't vote)."
    ),
)


# ============================================================================
# Tool Collection for Registry
# ============================================================================

CLI_VOTER_TOOLS = [
    # Configuration display
    show_voter_config_tool,
    explain_voting_logic_tool,
    explain_macd_params_tool,
    explain_rsi_params_tool,
    get_voter_parameters_tool,
    compare_with_traditional_tool,
    show_signal_interpretation_tool,
    # Ranked voting management (Issue #488)
    list_voters_tool,
    show_voting_info_tool,
    list_voter_presets_tool,
    apply_voter_preset_tool,
    promote_voter_tool,
    demote_voter_tool,
    set_voter_role_tool,
]

__all__ = [
    # Configuration display functions
    "show_voter_config",
    "explain_voting_logic",
    "explain_macd_params",
    "explain_rsi_params",
    "get_voter_parameters",
    "compare_with_traditional",
    "show_signal_interpretation",
    # Ranked voting management functions (Issue #488)
    "list_voters",
    "show_voting_info",
    "list_voter_presets",
    "apply_voter_preset",
    "promote_voter",
    "demote_voter",
    "set_voter_role",
    # Configuration display tools
    "show_voter_config_tool",
    "explain_voting_logic_tool",
    "explain_macd_params_tool",
    "explain_rsi_params_tool",
    "get_voter_parameters_tool",
    "compare_with_traditional_tool",
    "show_signal_interpretation_tool",
    # Ranked voting management tools (Issue #488)
    "list_voters_tool",
    "show_voting_info_tool",
    "list_voter_presets_tool",
    "apply_voter_preset_tool",
    "promote_voter_tool",
    "demote_voter_tool",
    "set_voter_role_tool",
    # Collection
    "CLI_VOTER_TOOLS",
]
