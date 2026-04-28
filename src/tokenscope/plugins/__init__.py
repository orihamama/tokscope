"""Plugin system: extractors, aggregators, detectors."""
from .base import Aggregator, Detector, Extractor, ExtractCtx
from .registry import registry

__all__ = ["registry", "Extractor", "Aggregator", "Detector", "ExtractCtx"]
