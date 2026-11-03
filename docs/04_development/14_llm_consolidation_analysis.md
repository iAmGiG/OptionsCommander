# LLM Service Consolidation Analysis

> **Historical analysis.** The agents, `BaseAgent` and the AutoGen `FunctionTool` code it discusses were retired (#606, #331). The one LLM call left is the natural-language parser, `src/parsers/llm_parser.py` (`LLMParser`).

**Issue**: #406
**Date**: November 2025

> **Update (Oct 2026, #606):** AutoGen is archived and was replaced by Microsoft Agent Framework. The parser described here is now `src/parsers/llm_parser.py` (`LLMParser`; `AutoGenLLMParser` remains an alias) and uses Agent Framework's OpenAI chat client. The analysis below is kept as written.

## Executive Summary

The codebase has **redundant LLM integrations** that should be consolidated. AutoGen already provides all the functionality we need via `OpenAIChatCompletionClient`. The custom `OpenAIService` is unnecessary overhead.

**Recommendation**: **Option A** - Remove custom service, use AutoGen client everywhere.

---

## Current State

### Custom LLM Service (`src/services/llm/`)

```python
# openai_service.py - Uses raw OpenAI SDK
from openai import AsyncOpenAI

class OpenAIService(LLMService):
    async def call_tool(self, prompt, tools, temperature=0.0) -> Dict:
        """Function calling via OpenAI API"""
        response = await self.client.chat.completions.create(
            model=self.tool_calling_model,
            messages=[{"role": "user", "content": prompt}],
            tools=[{"type": "function", "function": tool} for tool in tools],
            tool_choice="auto",
            temperature=temperature,
        )
        # Returns: {"function_name": "...", "arguments": {...}}

    async def reason(self, prompt, system_prompt=None, temperature=0.7) -> str:
        """Plain text generation"""
        # ...
```

**Used by**:

- `src/parsers/llm_parser.py` → `LLMParser.parse()` for NL input
- `src/core/factory.py` → `OrchestratorFactory.create()`

### AutoGen Native Client (`src/autogen_agents/base_agent.py`)

```python
# Uses AutoGen's OpenAI integration
from autogen_ext.models.openai import OpenAIChatCompletionClient

class BaseAgent(AssistantAgent, ABC):
    def __init__(self, ...):
        self.model_client = OpenAIChatCompletionClient(
            model=selected_model,
            api_key=open_ai_key,
            temperature=0.2,
            max_tokens=4096,
        )

    async def _run_tool_conversation(self, messages):
        """Tool calling via AutoGen"""
        response = await self.model_client.create(
            messages=conversation,
            tools=tools_list,  # <-- Same capability as OpenAIService.call_tool()!
        )
```

**Used by**:

- All AutoGen agents (VoterAgent, ScannerAgent, RiskAgent, etc.)

---

## Comparison

| Feature | Custom OpenAIService | AutoGen OpenAIChatCompletionClient |
|---------|---------------------|-----------------------------------|
| Tool/Function calling | `call_tool()` | `create(messages, tools=[...])` |
| Plain text generation | `reason()` | `create(messages)` |
| Dual model support | Manual (2 instances) | Via `use_dual_models` flag |
| Config loading | Manual | Already in `base_agent.py` |
| Error handling | Basic try/catch | Built into AutoGen framework |
| Async support | ✅ | ✅ |
| Message formatting | Manual | `UserMessage`, `SystemMessage` classes |

**Key Insight**: AutoGen's client does **everything** OpenAIService does, plus more:

- Automatic message formatting
- Tool result handling
- Multi-turn conversations
- Cancellation tokens
- Better error handling

---

## Options Analysis

### Option A: Remove custom service, use AutoGen client everywhere ✅ RECOMMENDED

**Changes**:

1. Update `LLMParser` to use `OpenAIChatCompletionClient` directly
2. Remove/deprecate `src/services/llm/` folder
3. Share config loading with `base_agent.py`

**Pros**:

- Single LLM integration path
- Reduced code complexity
- Unified configuration
- Leverages AutoGen's robust implementation

**Cons**:

- Migration effort (1-2 hours)
- May break end-to-end tests (need updating)

### Option B: Keep abstraction but use AutoGen under the hood

**Changes**:

1. Keep `LLMService` interface
2. Implement `AutoGenLLMService` using `OpenAIChatCompletionClient`
3. Deprecate `OpenAIService`

**Pros**:

- Maintains abstraction for future provider swaps
- Less disruptive to existing code

**Cons**:

- Still has redundant abstraction layer
- More complex than necessary
- AutoGen already provides provider abstraction

### Option C: Remove NL parsing entirely

**Changes**:

1. Remove `LLMParser` completely
2. Remove `src/services/llm/` folder
3. CLI uses pattern matching only

**Pros**:

- Simplest solution
- No LLM costs for parsing
- Current CLI works fine without it

**Cons**:

- Loses "buy 50 SPY" natural language support
- Less user-friendly CLI
- Removes planned feature

---

## Implementation Plan (Option A)

### Phase 1: Create AutoGen-based Parser

```python
# src/parsers/autogen_llm_parser.py (new)
from autogen_ext.models.openai import OpenAIChatCompletionClient
from autogen_core.models import UserMessage, SystemMessage

class AutoGenLLMParser(InputParser):
    def __init__(self, model_client: OpenAIChatCompletionClient = None):
        if model_client is None:
            # Use shared config from base_agent
            from src.autogen_agents.base_agent import tool_model_name, open_ai_key
            self.client = OpenAIChatCompletionClient(
                model=tool_model_name,
                api_key=open_ai_key,
                temperature=0.0,
            )
        else:
            self.client = model_client

    async def parse(self, user_input: str, user_id: Optional[str] = None) -> TradeRequest:
        messages = [UserMessage(content=self._build_prompt(user_input), source="user")]
        tools = [self._get_parse_tool()]  # Convert to AutoGen tool format

        response = await self.client.create(messages=messages, tools=tools)
        # Extract and return TradeRequest
```

### Phase 2: Update Factory

```python
# src/core/factory.py
from parsers import AutoGenLLMParser  # New import

class OrchestratorFactory:
    def create(self, ...):
        # Use AutoGen-based parser
        input_parser = AutoGenLLMParser()  # No separate LLM service needed
```

### Phase 3: Deprecate Old Service

```python
# src/services/llm/openai_service.py
import warnings

class OpenAIService(LLMService):
    def __init__(self, ...):
        warnings.warn(
            "OpenAIService is deprecated. Use AutoGen's OpenAIChatCompletionClient instead. "
            "See src/parsers/autogen_llm_parser.py for example.",
            DeprecationWarning,
            stacklevel=2
        )
        # ... existing code
```

### Phase 4: Update Tests

Update `tests/end_to_end/03_end_to_end.py` to use new parser.

### Phase 5: Remove Deprecated Code (Future)

After verification period, remove `src/services/llm/` folder entirely.

---

## Files Affected

| File | Action |
|------|--------|
| `src/parsers/autogen_llm_parser.py` | **CREATE** - New AutoGen-based parser |
| `src/parsers/__init__.py` | **UPDATE** - Export new parser |
| `src/core/factory.py` | **UPDATE** - Use new parser |
| `src/services/llm/openai_service.py` | **UPDATE** - Add deprecation warning |
| `src/services/llm/llm_service.py` | **UPDATE** - Add deprecation warning |
| `tests/end_to_end/03_end_to_end.py` | **UPDATE** - Use new parser |
| `docs/04_development/config_migration_notes.md` | **UPDATE** - Document migration |

---

## Migration Checklist

- [ ] Create `AutoGenLLMParser` class
- [ ] Add AutoGen tool format converter
- [ ] Update `OrchestratorFactory` to use new parser
- [ ] Add deprecation warnings to old service
- [ ] Update end-to-end tests
- [ ] Update documentation
- [ ] Verify `python main.py` still works
- [ ] Create PR with migration notes

---

## Appendix: Tool Format Comparison

### OpenAI SDK Format (current)

```python
tools = [{
    "name": "parse_trade_request",
    "description": "...",
    "parameters": { "type": "object", "properties": {...} }
}]
```

### AutoGen Format

```python
from autogen_core.tools import FunctionTool

def parse_trade_request(ticker: str, action: str, ...) -> dict:
    """Parse user's trade request into structured format"""
    return {"ticker": ticker, "action": action, ...}

tools = [FunctionTool(parse_trade_request)]
```

AutoGen's `FunctionTool` auto-generates the schema from type hints.
