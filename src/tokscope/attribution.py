"""Byte-weighted token attribution to tool calls."""

from __future__ import annotations


def split_proportional(total: int, weights: list[float]) -> list[int]:
    """Split `total` across N items by weight. Largest-remainder rounding."""
    if total <= 0 or not weights:
        return [0] * len(weights)
    s = sum(weights)
    if s <= 0:
        # equal split
        base = total // len(weights)
        out = [base] * len(weights)
        out[-1] += total - base * len(weights)
        return out
    raw = [total * w / s for w in weights]
    floors = [int(r) for r in raw]
    remainder = total - sum(floors)
    fracs = sorted(range(len(weights)), key=lambda i: raw[i] - floors[i], reverse=True)
    for i in fracs[:remainder]:
        floors[i] += 1
    return floors


def byte_size(obj) -> int:
    """Approximate JSON byte size for weighting."""
    import json

    try:
        return len(json.dumps(obj, ensure_ascii=False).encode("utf-8"))
    except Exception:
        return len(str(obj).encode("utf-8", errors="replace"))
