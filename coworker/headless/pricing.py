"""Token prices, used to estimate the cost of a headless run. An unknown model gets no
cost, never a guess; token counts are always recorded regardless.

The table (prices.yaml beside this file) is a cross-check, not the source of truth: a
run's cost is recomputed by whoever consumes the trajectory. Re-check the rows against
the vendors' pages before relying on them and update the dates in the file.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import yaml

_PRICES_PATH = Path(__file__).with_name("prices.yaml")
_NUMERIC = ("input", "output", "cache_read", "cache_write")


def load_prices() -> dict[str, dict[str, float]]:
    """{model id: {input, output, cache_read, cache_write}} in USD per million tokens."""
    try:
        data = yaml.safe_load(_PRICES_PATH.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return {}
    models = data.get("models") or {}
    return {
        str(k): {f: float((spec or {})[f]) for f in _NUMERIC if f in (spec or {})}
        for k, spec in models.items()
    }


def estimate_cost(
    model: str, usage: dict[str, Any], prices: Optional[dict] = None
) -> Optional[float]:
    """USD for one usage record {input, output, cache_read, cache_write} (token counts).
    Returns None when the model has no row."""
    prices = load_prices() if prices is None else prices
    spec = prices.get(model)
    if not spec:
        return None
    per_million = 1_000_000.0
    cost = 0.0
    cost += float(usage.get("input", 0) or 0) * spec.get("input", 0.0) / per_million
    cost += float(usage.get("output", 0) or 0) * spec.get("output", 0.0) / per_million
    cost += (
        float(usage.get("cache_read", 0) or 0)
        * spec.get("cache_read", spec.get("input", 0.0))
        / per_million
    )
    cost += (
        float(usage.get("cache_write", 0) or 0)
        * spec.get("cache_write", spec.get("input", 0.0))
        / per_million
    )
    return round(cost, 6)
