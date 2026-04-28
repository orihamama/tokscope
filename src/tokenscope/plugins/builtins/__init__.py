"""Built-in extractors, aggregators, and detectors.

Importing this package triggers registration of every built-in plugin via
their module-level @registry.register_* decorator calls.
"""
from . import extractors as _extractors  # noqa: F401
from . import aggregators as _aggregators  # noqa: F401
from . import detectors as _detectors  # noqa: F401
