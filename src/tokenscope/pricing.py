"""Model pricing from LiteLLM JSON, with offline cache and fuzzy match."""
from __future__ import annotations
import json
import time
from pathlib import Path
from typing import Any

import httpx

from .paths import PRICING_CACHE

LITELLM_URL = (
    "https://raw.githubusercontent.com/BerriAI/litellm/main/"
    "model_prices_and_context_window.json"
)
REFRESH_AFTER_S = 7 * 24 * 3600

# Hardcoded fallback for common Claude models (USD per token).
# Source: anthropic.com/pricing as of plan date.
FALLBACK_PRICES: dict[str, dict[str, float]] = {
    "claude-opus-4": {"input": 15e-6, "output": 75e-6, "cache_creation": 18.75e-6, "cache_read": 1.5e-6},
    "claude-opus-4-1": {"input": 15e-6, "output": 75e-6, "cache_creation": 18.75e-6, "cache_read": 1.5e-6},
    "claude-opus-4-5": {"input": 15e-6, "output": 75e-6, "cache_creation": 18.75e-6, "cache_read": 1.5e-6},
    "claude-opus-4-6": {"input": 15e-6, "output": 75e-6, "cache_creation": 18.75e-6, "cache_read": 1.5e-6},
    "claude-opus-4-7": {"input": 15e-6, "output": 75e-6, "cache_creation": 18.75e-6, "cache_read": 1.5e-6},
    "claude-sonnet-4": {"input": 3e-6, "output": 15e-6, "cache_creation": 3.75e-6, "cache_read": 0.3e-6},
    "claude-sonnet-4-5": {"input": 3e-6, "output": 15e-6, "cache_creation": 3.75e-6, "cache_read": 0.3e-6},
    "claude-sonnet-4-6": {"input": 3e-6, "output": 15e-6, "cache_creation": 3.75e-6, "cache_read": 0.3e-6},
    "claude-haiku-4-5": {"input": 1e-6, "output": 5e-6, "cache_creation": 1.25e-6, "cache_read": 0.1e-6},
    "claude-3-5-sonnet": {"input": 3e-6, "output": 15e-6, "cache_creation": 3.75e-6, "cache_read": 0.3e-6},
    "claude-3-5-haiku": {"input": 0.8e-6, "output": 4e-6, "cache_creation": 1e-6, "cache_read": 0.08e-6},
    "claude-3-opus": {"input": 15e-6, "output": 75e-6, "cache_creation": 18.75e-6, "cache_read": 1.5e-6},
}


def _load_litellm() -> dict[str, Any]:
    if PRICING_CACHE.exists():
        age = time.time() - PRICING_CACHE.stat().st_mtime
        if age < REFRESH_AFTER_S:
            try:
                return json.loads(PRICING_CACHE.read_text())
            except Exception:
                pass
    try:
        r = httpx.get(LITELLM_URL, timeout=10)
        r.raise_for_status()
        PRICING_CACHE.parent.mkdir(parents=True, exist_ok=True)
        PRICING_CACHE.write_text(r.text)
        return r.json()
    except Exception:
        if PRICING_CACHE.exists():
            return json.loads(PRICING_CACHE.read_text())
        return {}


_PRICES: dict[str, dict[str, float]] | None = None


def _normalize_litellm(raw: dict[str, Any]) -> dict[str, dict[str, float]]:
    out: dict[str, dict[str, float]] = {}
    for name, info in raw.items():
        if not isinstance(info, dict):
            continue
        if "input_cost_per_token" not in info:
            continue
        out[name] = {
            "input": float(info.get("input_cost_per_token") or 0),
            "output": float(info.get("output_cost_per_token") or 0),
            "cache_creation": float(
                info.get("cache_creation_input_token_cost")
                or info.get("input_cost_per_token", 0) * 1.25
            ),
            "cache_read": float(
                info.get("cache_read_input_token_cost")
                or info.get("input_cost_per_token", 0) * 0.1
            ),
        }
    return out


def _all_prices() -> dict[str, dict[str, float]]:
    global _PRICES
    if _PRICES is None:
        prices = dict(FALLBACK_PRICES)
        prices.update(_normalize_litellm(_load_litellm()))
        _PRICES = prices
    return _PRICES


def price_for(model: str | None) -> dict[str, float]:
    """Return per-token prices. Fuzzy match by substring."""
    if not model:
        return {"input": 0.0, "output": 0.0, "cache_creation": 0.0, "cache_read": 0.0}
    prices = _all_prices()
    if model in prices:
        return prices[model]
    # exact prefix
    for key in prices:
        if model.startswith(key):
            return prices[key]
    # substring fuzzy match
    lo = model.lower()
    candidates = [k for k in prices if k.lower() in lo or lo in k.lower()]
    if candidates:
        candidates.sort(key=len, reverse=True)
        return prices[candidates[0]]
    # generic family match
    for fam in ("opus", "sonnet", "haiku"):
        if fam in lo:
            for k in prices:
                if fam in k.lower():
                    return prices[k]
    return {"input": 0.0, "output": 0.0, "cache_creation": 0.0, "cache_read": 0.0}


def calc_cost(
    model: str | None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_creation: int = 0,
    cache_read: int = 0,
) -> float:
    p = price_for(model)
    return (
        input_tokens * p["input"]
        + output_tokens * p["output"]
        + cache_creation * p["cache_creation"]
        + cache_read * p["cache_read"]
    )
