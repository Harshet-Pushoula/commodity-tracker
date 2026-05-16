# config.py
# Single source of truth for all assets, validation rules, and fallback sources.
# Edit thresholds here — never inside fetch.py.

ASSETS = {
    # --- Commodities (via yfinance futures tickers) ---
    "oil": {
        "ticker": "BZ=F",
        "source": "yfinance",
        "unit": "USD/barrel",
        "display": "Brent Crude",
        "category": "commodity",
    },
    "gold": {
        "ticker": "GC=F",
        "source": "yfinance",
        "unit": "USD/troy oz",
        "display": "Gold",
        "category": "commodity",
    },
    "wheat": {
        "ticker": "ZW=F",
        "source": "yfinance",
        "unit": "USD/bushel",
        "display": "Wheat",
        "category": "commodity",
    },
    "natgas": {
        "ticker": "NG=F",
        "source": "yfinance",
        "unit": "USD/MMBtu",
        "display": "Natural Gas",
        "category": "commodity",
    },

    # --- FX pairs (via frankfurter.app — ECB reference rates, updates daily) ---
    "eurusd": {
        "pair": "EUR/USD",
        "base": "EUR",
        "quote": "USD",
        "source": "frankfurter",
        "unit": "USD per EUR",
        "display": "EUR / USD",
        "category": "fx",
        "note": "ECB daily rate",
    },
    "gbpusd": {
        "pair": "GBP/USD",
        "base": "GBP",
        "quote": "USD",
        "source": "frankfurter",
        "unit": "USD per GBP",
        "display": "GBP / USD",
        "category": "fx",
        "note": "ECB daily rate",
    },
    "usdinr": {
        "pair": "USD/INR",
        "base": "USD",
        "quote": "INR",
        "source": "frankfurter",
        "unit": "INR per USD",
        "display": "USD / INR",
        "category": "fx",
        "note": "ECB daily rate",
    },
    "usdcny": {
        "pair": "USD/CNY",
        "base": "USD",
        "quote": "CNY",
        "source": "frankfurter",
        "unit": "CNY per USD",
        "display": "USD / CNY",
        "category": "fx",
        "note": "ECB daily rate",
    },
}

# Per-asset validation thresholds.
# max_change is the maximum absolute daily % change before we flag the value as suspect.
# min_price / max_price are hard bounds — anything outside is almost certainly bad data.
VALIDATION_RULES = {
    "oil":    {"min_price": 10,   "max_price": 300,  "max_change": 0.20},
    "gold":   {"min_price": 500,  "max_price": 6000, "max_change": 0.08},
    "wheat":  {"min_price": 150,  "max_price": 3000, "max_change": 0.12},
    "natgas": {"min_price": 0.5,  "max_price": 50,   "max_change": 0.20},
    "eurusd": {"min_price": 0.80, "max_price": 1.60, "max_change": 0.03},
    "gbpusd": {"min_price": 0.90, "max_price": 1.80, "max_change": 0.03},
    "usdinr": {"min_price": 60,   "max_price": 110,  "max_change": 0.03},
    "usdcny": {"min_price": 5.0,  "max_price": 9.0,  "max_change": 0.03},
}

# Secondary API fallbacks per asset.
# These are called only if the primary source fails or returns invalid data.
# open.er-api.com is free with no API key for FX.
# For commodities we don't have a keyless secondary — they fall back to stale.
FALLBACK_SOURCES = {
    "eurusd": "https://open.er-api.com/v6/latest/EUR",
    "gbpusd": "https://open.er-api.com/v6/latest/GBP",
    "usdinr": "https://open.er-api.com/v6/latest/USD",
    "usdcny": "https://open.er-api.com/v6/latest/USD",
}

# How many days of history to keep in history.json before trimming.
HISTORY_DAYS = 90

# How many data points to include in the sparkline array inside latest.json.
SPARKLINE_POINTS = 30

# If this fraction of assets fail in a single run, abort the write entirely.
ABORT_THRESHOLD = 0.5
