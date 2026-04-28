"""Plugin system: extractors, aggregators, detectors."""

from .base import Aggregator, Detector, ExtractCtx, Extractor
from .registry import registry

__all__ = ["Aggregator", "Detector", "ExtractCtx", "Extractor", "registry"]
