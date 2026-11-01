# Agent Ensemble (Retired)

OptionsCommander no longer has trading agents. They were retired in #331: nothing a routine or a command ran used them, and the framework work (#606) left them with no reachable path.

## What Runs Instead

| Job | Where | Notes |
| --- | ----- | ----- |
| MACD+RSI vote | `src/trading/instruments/voting_signal.py` (`VotingSignal`) | Plain Python, no LLM. The CLI's signal; the backtest shows no edge ([live-rules baseline](../08_research/03_strategy_research/live_rules_baseline.md)) |
| Multi-timeframe vote | `src/trading/instruments/multi_timeframe_voter.py` | `VotingSignal` across timeframes, with presets |
| Analysis for a request | `src/strategies/real_voter_strategy.py` (`RealVoterStrategy`) | Fetches data, runs the vote, plans entry, stop and target |
| Risk checks and orders | `src/core/`, `src/trading/broker/` | Reached from the CLI on request |
| Stops and records | `src/trading/scheduling/trading_cycle.py` | The morning and evening routines |
| Parsing typed requests | `src/parsers/llm_parser.py` (`LLMParser`) | The one LLM call |

## History

The system started as sentiment agents (V0-V4), moved to a voting agent with scanner, risk and executor agents planned around it, and ran on AutoGen until #606 moved it to Microsoft Agent Framework. The planned agents were never reached by a user or a routine. #650 removed the unreachable ones, and #331 finished the job once the voting rules lived in `VotingSignal`. The code is in git history before that removal.
