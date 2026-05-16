#!/usr/bin/env python3
"""
fetch.py — Global Commodity & Currency Tracker
Fetches asset prices, validates them, falls back gracefully, writes JSON output.

Run manually:  python fetch.py
Run in CI:     called by .github/workflows/fetch.yml
"""

import json
import math
import os
import traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path

import requests
import yfinance as yf

from config import (
    ASSETS,
    VALIDATION_RULES,
    FALLBACK_SOURCES,
    HISTORY_DAYS,
    SPARKLINE_POINTS,
    ABORT_THRESHOLD,
)

# ── Paths ────────────────────────────────────────────────────────────────────
ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
LATEST_PATH = DATA_DIR / "latest.json"
HISTORY_PATH = DATA_DIR / "history.json"
ERRORS_PATH = DATA_DIR / "errors.json"

DATA_DIR.mkdir(exist_ok=True)


# ── Helpers ──────────────────────────────────────────────────────────────────

def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_json(path: Path, default):
    """Load JSON from path, returning default if missing or corrupt."""
    try:
        if path.exists():
            return json.loads(path.read_text())
    except Exception:
        pass
    return default


def is_valid_float(value) -> bool:
    """Return True only if value is a finite, non-NaN float-like."""
    try:
        f = float(value)
        return math.isfinite(f) and f > 0
    except (TypeError, ValueError):
        return False


def validate(asset_key: str, price, change_pct) -> tuple[bool, str]:
    """
    Validate price and daily change against per-asset rules.
    Returns (is_valid, reason_string).
    """
    rules = VALIDATION_RULES.get(asset_key)
    if not rules:
        return False, "no_rules_defined"

    if not is_valid_float(price):
        return False, f"invalid_price_value:{price}"

    price = float(price)

    if price < rules["min_price"]:
        return False, f"price_too_low:{price}<{rules['min_price']}"
    if price > rules["max_price"]:
        return False, f"price_too_high:{price}>{rules['max_price']}"

    if change_pct is not None and is_valid_float(change_pct):
        if abs(float(change_pct)) > rules["max_change"]:
            return False, f"change_exceeds_threshold:{change_pct:.4f}>{rules['max_change']}"

    return True, "ok"


# ── Primary fetch: yfinance (commodities) ────────────────────────────────────

def fetch_yfinance(asset_key: str) -> dict | None:
    """
    Fetch price and daily change from yfinance.
    Returns {"price": float, "change_pct": float} or None on failure.
    """
    cfg = ASSETS[asset_key]
    ticker = cfg["ticker"]
    try:
        t = yf.Ticker(ticker)
        info = t.fast_info
        price = info.last_price
        prev_close = info.previous_close

        if not is_valid_float(price):
            return None

        change_pct = None
        if is_valid_float(prev_close) and float(prev_close) > 0:
            change_pct = (float(price) - float(prev_close)) / float(prev_close)

        return {"price": float(price), "change_pct": change_pct}
    except Exception as e:
        return None


# ── Primary fetch: frankfurter.app (FX) ──────────────────────────────────────

def fetch_frankfurter(asset_key: str) -> dict | None:
    """
    Fetch FX rate from frankfurter.app (ECB reference rates).
    Returns {"price": float, "change_pct": None} or None on failure.
    Note: frankfurter.app doesn't provide daily % change directly.
    """
    cfg = ASSETS[asset_key]
    base = cfg["base"]
    quote = cfg["quote"]
    try:
        # Latest rate
        r = requests.get(
            f"https://api.frankfurter.app/latest?from={base}&to={quote}",
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        price = data["rates"][quote]

        if not is_valid_float(price):
            return None

        # Try to get yesterday's rate for % change
        change_pct = None
        try:
            yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
            r2 = requests.get(
                f"https://api.frankfurter.app/{yesterday}?from={base}&to={quote}",
                timeout=10,
            )
            r2.raise_for_status()
            prev_price = r2.json()["rates"][quote]
            if is_valid_float(prev_price) and float(prev_price) > 0:
                change_pct = (float(price) - float(prev_price)) / float(prev_price)
        except Exception:
            pass  # change_pct stays None — not critical

        return {"price": float(price), "change_pct": change_pct}
    except Exception:
        return None


# ── Fallback fetch: open.er-api.com (FX only) ────────────────────────────────

def fetch_open_er(asset_key: str) -> dict | None:
    """
    Fallback FX fetch from open.er-api.com (free, no key).
    Returns {"price": float, "change_pct": None} or None on failure.
    """
    cfg = ASSETS[asset_key]
    base = cfg["base"]
    quote = cfg["quote"]
    url = FALLBACK_SOURCES.get(asset_key)
    if not url:
        return None
    try:
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        data = r.json()
        rates = data.get("rates", {})
        price = rates.get(quote)
        if not is_valid_float(price):
            return None
        return {"price": float(price), "change_pct": None}
    except Exception:
        return None


# ── Three-tier fetch per asset ────────────────────────────────────────────────

def fetch_asset(asset_key: str, previous_output: dict) -> dict:
    """
    Attempt to fetch an asset using the three-tier strategy:
      1. Primary source (yfinance or frankfurter)
      2. Secondary source (open.er-api, where available)
      3. Stale fallback (last known good value from previous run)

    Returns a fully-formed asset dict ready to write to latest.json.
    """
    cfg = ASSETS[asset_key]
    source = cfg["source"]
    errors = []

    # ── Tier 1: Primary ──
    raw = None
    if source == "yfinance":
        raw = fetch_yfinance(asset_key)
    elif source == "frankfurter":
        raw = fetch_frankfurter(asset_key)

    if raw:
        valid, reason = validate(asset_key, raw["price"], raw["change_pct"])
        if valid:
            return _build_record(asset_key, raw["price"], raw["change_pct"], "fresh", source)
        else:
            errors.append(f"primary_validation_failed:{reason}")
    else:
        errors.append("primary_fetch_failed")

    # ── Tier 2: Secondary ──
    if asset_key in FALLBACK_SOURCES:
        raw2 = fetch_open_er(asset_key)
        if raw2:
            valid, reason = validate(asset_key, raw2["price"], raw2["change_pct"])
            if valid:
                return _build_record(asset_key, raw2["price"], raw2["change_pct"], "fresh", "open.er-api.com (fallback)")
            else:
                errors.append(f"secondary_validation_failed:{reason}")
        else:
            errors.append("secondary_fetch_failed")

    # ── Tier 3: Stale fallback ──
    prev_assets = previous_output.get("assets", {})
    prev = prev_assets.get(asset_key)
    if prev and is_valid_float(prev.get("price")):
        stale_since = prev.get("stale_since") or prev.get("fetched_at") or now_utc()
        return _build_record(
            asset_key,
            prev["price"],
            prev.get("change_pct"),
            "stale",
            prev.get("source", "cached"),
            stale_since=stale_since,
            fetch_errors=errors,
        )

    # ── Tier 4: Truly unavailable ──
    return _build_record(asset_key, None, None, "unavailable", None, fetch_errors=errors)


def _build_record(
    asset_key: str,
    price,
    change_pct,
    status: str,
    source: str | None,
    stale_since: str | None = None,
    fetch_errors: list | None = None,
) -> dict:
    """Assemble a single asset record for latest.json."""
    cfg = ASSETS[asset_key]
    record = {
        "key": asset_key,
        "display": cfg["display"],
        "category": cfg["category"],
        "unit": cfg["unit"],
        "note": cfg.get("note"),
        "price": round(float(price), 4) if is_valid_float(price) else None,
        "change_pct": round(float(change_pct), 6) if (change_pct is not None and is_valid_float(change_pct)) else None,
        "status": status,
        "stale": status == "stale",
        "stale_since": stale_since if status == "stale" else None,
        "source": source,
        "fetched_at": now_utc(),
    }
    if fetch_errors:
        record["fetch_errors"] = fetch_errors
    return record


# ── History management ────────────────────────────────────────────────────────

def load_history() -> list:
    return load_json(HISTORY_PATH, [])


def trim_history(history: list) -> list:
    """Remove entries older than HISTORY_DAYS."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=HISTORY_DAYS)
    result = []
    for entry in history:
        try:
            ts = datetime.fromisoformat(entry["timestamp"])
            if ts >= cutoff:
                result.append(entry)
        except Exception:
            pass
    return result


def build_sparklines(history: list) -> dict:
    """
    Extract the last SPARKLINE_POINTS price values per asset from history.
    Returns {"oil": [72.1, 72.4, ...], "gold": [...], ...}
    """
    sparklines = {key: [] for key in ASSETS}
    # History is oldest-first; we want last N points
    for entry in history[-SPARKLINE_POINTS:]:
        for key in ASSETS:
            asset = entry.get("assets", {}).get(key, {})
            price = asset.get("price")
            if is_valid_float(price):
                sparklines[key].append(float(price))
    return sparklines


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    run_timestamp = now_utc()
    print(f"[{run_timestamp}] Starting fetch run...")

    # Load previous output for stale fallback
    previous_output = load_json(LATEST_PATH, {})

    # Fetch all assets
    results = {}
    errors_log = {}

    for asset_key in ASSETS:
        print(f"  Fetching {asset_key}...", end=" ")
        try:
            record = fetch_asset(asset_key, previous_output)
            results[asset_key] = record
            status = record["status"]
            price = record.get("price")
            print(f"{status.upper()} — {price}")
            if record.get("fetch_errors"):
                errors_log[asset_key] = {
                    "timestamp": run_timestamp,
                    "errors": record["fetch_errors"],
                    "status": status,
                }
        except Exception as e:
            print(f"EXCEPTION — {e}")
            errors_log[asset_key] = {
                "timestamp": run_timestamp,
                "errors": [f"unhandled_exception:{str(e)}"],
                "status": "unavailable",
            }
            results[asset_key] = _build_record(asset_key, None, None, "unavailable", None)

    # Abort check: if too many assets are unavailable, don't overwrite latest.json
    unavailable = sum(1 for r in results.values() if r["status"] == "unavailable")
    total = len(results)
    if unavailable / total >= ABORT_THRESHOLD:
        print(f"\nABORTING: {unavailable}/{total} assets unavailable (>= {ABORT_THRESHOLD*100:.0f}%). Not writing latest.json.")
        _write_errors(errors_log, run_timestamp, aborted=True)
        return

    # Load and trim history
    history = load_history()
    history = trim_history(history)

    # Build sparklines from trimmed history
    sparklines = build_sparklines(history)

    # Inject sparklines into records
    for key, record in results.items():
        record["sparkline"] = sparklines.get(key, [])

    # Assemble latest.json output
    output = {
        "last_updated": run_timestamp,
        "assets": results,
    }

    # Append this run to history (without sparklines to keep history lean)
    history_entry = {
        "timestamp": run_timestamp,
        "assets": {
            k: {"price": v["price"], "change_pct": v["change_pct"], "status": v["status"]}
            for k, v in results.items()
        },
    }
    history.append(history_entry)

    # Write files
    LATEST_PATH.write_text(json.dumps(output, indent=2))
    HISTORY_PATH.write_text(json.dumps(history, indent=2))
    _write_errors(errors_log, run_timestamp)

    fresh = sum(1 for r in results.values() if r["status"] == "fresh")
    stale = sum(1 for r in results.values() if r["status"] == "stale")
    print(f"\nDone. {fresh} fresh, {stale} stale, {unavailable} unavailable.")
    print(f"latest.json written ({LATEST_PATH.stat().st_size} bytes)")
    print(f"history.json has {len(history)} entries")


def _write_errors(errors_log: dict, run_timestamp: str, aborted: bool = False):
    existing = load_json(ERRORS_PATH, [])
    if errors_log or aborted:
        existing.append({
            "run": run_timestamp,
            "aborted": aborted,
            "assets": errors_log,
        })
    # Keep last 100 error runs only
    existing = existing[-100:]
    ERRORS_PATH.write_text(json.dumps(existing, indent=2))


if __name__ == "__main__":
    main()
