"""Plugin registry — loads built-ins, entry-points, and user-dir plugins."""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import os
from pathlib import Path
from typing import Any

from .base import Aggregator, Detector, Extractor

USER_PLUGIN_DIR = Path(
    os.environ.get("TOKENSCOPE_PLUGIN_DIR") or Path.home() / ".config" / "tokenscope" / "plugins"
)

ENTRY_POINT_GROUPS = {
    "extractors": "tokenscope.extractors",
    "aggregators": "tokenscope.aggregators",
    "detectors": "tokenscope.detectors",
}


class Registry:
    def __init__(self) -> None:
        self.extractors: dict[str, Extractor] = {}
        self.aggregators: dict[str, Aggregator] = {}
        self.detectors: dict[str, Detector] = {}
        self._loaded = False

    # ----- registration decorators (used by built-in modules) ---------

    def register_extractor(self, ex: Extractor) -> Extractor:
        if not isinstance(ex, Extractor):
            raise TypeError(f"{ex!r} does not satisfy Extractor protocol")
        self.extractors[ex.name] = ex
        return ex

    def register_aggregator(self, ag: Aggregator) -> Aggregator:
        if not isinstance(ag, Aggregator):
            raise TypeError(f"{ag!r} does not satisfy Aggregator protocol")
        self.aggregators[ag.name] = ag
        return ag

    def register_detector(self, det: Detector) -> Detector:
        if not isinstance(det, Detector):
            raise TypeError(f"{det!r} does not satisfy Detector protocol")
        self.detectors[det.name] = det
        return det

    # ----- discovery --------------------------------------------------

    def load_all(self) -> None:
        if self._loaded:
            return
        self._load_builtins()
        self._load_entry_points()
        self._load_user_dir()
        self._loaded = True

    def _load_builtins(self) -> None:
        # Importing the package triggers @register decorators in each module.
        importlib.import_module("tokenscope.plugins.builtins")

    def _load_entry_points(self) -> None:
        try:
            eps = importlib.metadata.entry_points()
        except Exception:
            return
        for kind, group in ENTRY_POINT_GROUPS.items():
            try:
                group_eps = eps.select(group=group)
            except AttributeError:  # py < 3.10 fallback
                group_eps = eps.get(group, [])
            for ep in group_eps:
                try:
                    plugin = ep.load()
                except Exception:
                    continue
                # Allow both classes and instances
                instance = plugin() if isinstance(plugin, type) else plugin
                {
                    "extractors": self.register_extractor,
                    "aggregators": self.register_aggregator,
                    "detectors": self.register_detector,
                }[kind](instance)

    def _load_user_dir(self) -> None:
        d = USER_PLUGIN_DIR
        if not d.exists() or not d.is_dir():
            return
        for f in d.glob("*.py"):
            if f.name.startswith("_"):
                continue
            mod_name = f"_user_{f.stem}"
            spec = importlib.util.spec_from_file_location(mod_name, f)
            if spec is None or spec.loader is None:
                continue
            mod = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(mod)
            except Exception:
                continue
            # User plugin modules are expected to import `registry` and
            # call register_*() themselves.

    # ----- helpers ----------------------------------------------------

    def list_summaries(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "extractors": [
                {
                    "name": e.name,
                    "version": e.version,
                    "targets": list(e.targets),
                    "fields": e.fields(),
                }
                for e in self.extractors.values()
            ],
            "aggregators": [
                {"name": a.name, "version": a.version, "produces": list(a.produces)}
                for a in self.aggregators.values()
            ],
            "detectors": [
                {
                    "name": d.name,
                    "title": d.title,
                    "description": d.description,
                    "params_schema": d.params_schema,
                    "requires": list(d.requires),
                }
                for d in self.detectors.values()
            ],
        }


# Module-level singleton.
registry = Registry()
