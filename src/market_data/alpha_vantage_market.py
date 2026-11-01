"""
Tool for fetching market data from Alpha Vantage API.

This module specializes in retrieving market data (stock prices, company overviews)
from Alpha Vantage. It's part of the specialized data sources organization that
separates different types of financial data.
"""

import logging
import os
import re
from typing import Any, Dict, Optional

import pandas as pd
import requests

from src.cache import TradingCacheManager
from src.utils.config_loader import ConfigLoader
from src.utils.date_utils import (
    get_datetime_now,
    get_default_timezone,
    get_processed_date_range,
    localize_df,
    parse_date_string,
)
from src.utils.http_timeouts import DEFAULT_TIMEOUT

_API_KEY_IN_URL = re.compile(r"(apikey=)[^&\s'\"]+", re.IGNORECASE)


def _safe_error(error: Exception) -> str:
    """An exception's text without the API key: a requests error includes the whole URL (#638)."""
    return _API_KEY_IN_URL.sub(r"\1<hidden>", str(error))


class AlphaVantageMarketTool:
    """
    Tool for retrieving market data from Alpha Vantage API.

    This class focuses on market-specific data including:
    - Stock price history
    - Company overview/fundamentals

    Uses SQLite cache (TradingCacheManager) for efficient storage.
    """

    def __init__(self, cache_manager: Optional[TradingCacheManager] = None):
        # Load API key from environment or config
        config_loader = ConfigLoader()
        self.api_key = os.getenv("ALPHA_VANTAGE_KEY", config_loader.get("ALPHA_VANTAGE_KEY"))

        if not self.api_key:
            logging.warning("Alpha Vantage API key not found in config.")

        self.base_url = "https://www.alphavantage.co/query"
        self.logger = logging.getLogger(self.__class__.__name__)

        # Initialize SQLite cache
        self.cache = cache_manager or TradingCacheManager()

    def fetch_stock_data(
        self, symbol: str, start_date: Optional[str] = None, end_date: Optional[str] = None
    ) -> pd.DataFrame:
        """
        Fetch daily stock data for a given symbol.

        Args:
            symbol: Stock ticker symbol
            start_date: Optional start date filter (YYYY-MM-DD) or relative date string ("-30d")
                        If None, uses dynamic calculation (last 5 trading days)
            end_date: Optional end date filter (YYYY-MM-DD) or relative date string ("today")
                      If None, uses today's date

        Returns:
            DataFrame with stock price data
        """
        try:
            # Process date parameters, applying dynamic date calculation if needed
            processed_start, processed_end = get_processed_date_range(start_date, end_date)

            # Check cache first
            cached_data = self.cache.get(
                symbol, processed_start, processed_end, source="alpha_vantage"
            )
            if cached_data is not None:
                self.logger.info(f"Using cached Alpha Vantage data for {symbol}")
                return cached_data

            self.logger.info(
                f"Fetching Alpha Vantage data for {symbol} from {processed_start} to {processed_end}"
            )

            # Determine outputsize based on date range
            days_range = (get_datetime_now() - parse_date_string(processed_start)).days
            use_full = days_range > 100

            # API parameters for daily time series
            params = {
                "function": "TIME_SERIES_DAILY",
                "symbol": symbol,
                "apikey": self.api_key,
                "outputsize": "full" if use_full else "compact",
                "datatype": "json",
            }

            # Make API request
            response = requests.get(self.base_url, params=params, timeout=DEFAULT_TIMEOUT)

            if response.status_code != 200:
                self.logger.error(
                    f"Alpha Vantage API error: {response.status_code} - {response.text}"
                )
                return pd.DataFrame()

            data = response.json()

            # Check for errors in the response
            if "Error Message" in data:
                self.logger.debug(f"Alpha Vantage API error: {data['Error Message']}")
                return pd.DataFrame()

            # Extract time series data
            if "Time Series (Daily)" not in data:
                self.logger.warning(f"No time series data found for {symbol}")
                return pd.DataFrame()

            time_series = data["Time Series (Daily)"]

            # Convert to DataFrame
            df = pd.DataFrame.from_dict(time_series, orient="index")

            # Fix column names (removing number prefixes)
            df = df.rename(
                columns={
                    "1. open": "open",
                    "2. high": "high",
                    "3. low": "low",
                    "4. close": "close",
                    "5. volume": "volume",
                }
            )

            # Convert index to datetime
            df.index = pd.to_datetime(df.index)

            # Convert values to numeric
            for col in df.columns:
                df[col] = pd.to_numeric(df[col])  # pylint: disable=unsupported-assignment-operation,unsubscriptable-object

            # Apply date filters using processed dates
            df = df[df.index >= processed_start]  # pylint: disable=unsubscriptable-object
            df = df[df.index <= processed_end]

            # Sort by date (newest first)
            df = df.sort_index(ascending=False)

            df = localize_df(df, get_default_timezone())

            # Cache the data for future use
            self.cache.set(symbol, df, source="alpha_vantage")

            return df

        except Exception as e:
            self.logger.debug(f"Error fetching Alpha Vantage data: {_safe_error(e)}")
            return pd.DataFrame()

    def fetch_company_overview(self, symbol: str) -> Dict[str, Any]:
        """
        Fetch company overview data with fundamentals.

        Args:
            symbol: Stock ticker symbol

        Returns:
            Dictionary with company overview data
        """
        try:
            params = {"function": "OVERVIEW", "symbol": symbol, "apikey": self.api_key}

            response = requests.get(self.base_url, params=params, timeout=DEFAULT_TIMEOUT)

            if response.status_code != 200:
                self.logger.error(
                    f"Alpha Vantage API error: {response.status_code} - {response.text}"
                )
                return {}

            data = response.json()

            # Check for errors
            if "Error Message" in data:
                self.logger.debug(f"Alpha Vantage API error: {data['Error Message']}")
                return {}

            return data

        except Exception as e:
            self.logger.error(f"Error fetching company overview: {_safe_error(e)}")
            return {}

    def fetch_intraday_data(self, symbol: str, interval: str = "15min") -> pd.DataFrame:
        """
        Fetch intraday stock data for a given symbol.

        Args:
            symbol: Stock ticker symbol
            interval: Time interval between data points (1min, 5min, 15min, 30min, 60min)

        Returns:
            DataFrame with intraday price data
        """
        try:
            params = {
                "function": "TIME_SERIES_INTRADAY",
                "symbol": symbol,
                "interval": interval,
                "apikey": self.api_key,
                "outputsize": "compact",
                "datatype": "json",
            }

            response = requests.get(self.base_url, params=params, timeout=DEFAULT_TIMEOUT)

            if response.status_code != 200:
                self.logger.error(
                    f"Alpha Vantage API error: {response.status_code} - {response.text}"
                )
                return pd.DataFrame()

            data = response.json()

            # Check for errors
            if "Error Message" in data:
                self.logger.debug(f"Alpha Vantage API error: {data['Error Message']}")
                return pd.DataFrame()

            # Extract time series data
            time_series_key = f"Time Series ({interval})"
            if time_series_key not in data:
                self.logger.warning(f"No intraday data found for {symbol}")
                return pd.DataFrame()

            time_series = data[time_series_key]

            # Convert to DataFrame
            df = pd.DataFrame.from_dict(time_series, orient="index")

            # Fix column names
            df = df.rename(
                columns={
                    "1. open": "open",
                    "2. high": "high",
                    "3. low": "low",
                    "4. close": "close",
                    "5. volume": "volume",
                }
            )

            # Convert index to datetime
            df.index = pd.to_datetime(df.index)

            # Convert values to numeric
            for col in df.columns:
                df[col] = pd.to_numeric(df[col])  # pylint: disable=unsupported-assignment-operation,unsubscriptable-object

            # Sort by date (newest first)
            df = df.sort_index(ascending=False)

            return df

        except Exception as e:
            self.logger.error(f"Error fetching intraday data: {_safe_error(e)}")
            return pd.DataFrame()

    def fetch_fx_data(self, from_currency: str, to_currency: str) -> pd.DataFrame:
        """
        Fetch foreign exchange rate data.

        Args:
            from_currency: From currency code (e.g., USD)
            to_currency: To currency code (e.g., EUR)

        Returns:
            DataFrame with exchange rate data
        """
        try:
            params = {
                "function": "CURRENCY_EXCHANGE_RATE",
                "from_currency": from_currency,
                "to_currency": to_currency,
                "apikey": self.api_key,
            }

            response = requests.get(self.base_url, params=params, timeout=DEFAULT_TIMEOUT)

            if response.status_code != 200:
                self.logger.error(
                    f"Alpha Vantage API error: {response.status_code} - {response.text}"
                )
                return pd.DataFrame()

            data = response.json()

            # Check for errors
            if "Error Message" in data:
                self.logger.debug(f"Alpha Vantage API error: {data['Error Message']}")
                return pd.DataFrame()

            # Extract exchange rate data
            if "Realtime Currency Exchange Rate" not in data:
                self.logger.warning(
                    f"No exchange rate data found for {from_currency}/{to_currency}"
                )
                return pd.DataFrame()

            exchange_data = data["Realtime Currency Exchange Rate"]

            # Create DataFrame with a single row
            df = pd.DataFrame([exchange_data])

            return df

        except Exception as e:
            self.logger.error(f"Error fetching exchange rate data: {_safe_error(e)}")
            return pd.DataFrame()
