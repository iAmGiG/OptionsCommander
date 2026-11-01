# Utils Directory

This directory contains general-purpose utility modules used throughout the AutoTrader codebase.

## Modules

### `date_utils.py`

Centralized date and time utilities for consistent timezone handling across the trading system:

**Core Functions**:

- `get_datetime_now(tz=None)` - Timezone-aware current datetime
- `now_iso()` - Current time as ISO format string
- `parse_date_string(date_str)` - Flexible date string parsing
- `subtract_days(dt, days)` - Date arithmetic

**Trading-Specific**:

- `get_default_timezone()` - Get market timezone (America/New_York)
- `calculate_days_to_expiration(expiration_date)` - Options expiration calculations
- `is_opex_week(date)` - Check if date is in options expiration week
- `get_market_open_time()` / `get_market_close_time()` - Market hours

**Data Processing**:

- `process_date_param()` - Process date parameters from various formats
- `get_processed_date_range()` - Get processed date range with timezone handling
- `localize_df()` - Localize DataFrame index to timezone
- `get_datetime_from_timestamp(ts)` - Convert Unix timestamp to datetime

### `output_manager.py`

Output management for organized test results:

- `OutputManager` - Manages organized output structure for backtest results
- Creates timestamped directories with structured subdirectories
- Captures LLM reasoning and generates reports
- Extracts best insights from analysis

## Usage

```python
# Import utilities as needed
from src.utils.date_utils import process_date_param
from src.utils.output_manager import OutputManager
```

## Note

These utilities were moved from `src/tools/` to provide better separation of concerns:

- `/src/tools/` - Contains tool implementations and tool-specific code
- `/src/utils/` - Contains general-purpose utilities used across the codebase
