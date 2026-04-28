"""Filesystem path constants."""

from __future__ import annotations

import os
from pathlib import Path

CLAUDE_HOME = Path(os.environ.get("CLAUDE_HOME", Path.home() / ".claude"))
PROJECTS_DIR = CLAUDE_HOME / "projects"
DB_PATH = Path(os.environ.get("CLAUDE_ANALYTICS_DB", CLAUDE_HOME / "analytics.db"))
PRICING_CACHE = CLAUDE_HOME / "analytics-pricing.json"
