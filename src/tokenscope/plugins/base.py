"""Plugin protocols: Extractor, Aggregator, Detector.

Three plugin types extend tokenscope without forking:

- Extractor — runs per JSONL record at ingest, populates columns on
  `messages` or `tool_calls`.
- Aggregator — runs after raw ingest, builds derived entity tables
  (`api_requests`, `file_accesses`, `search_queries`, `task_templates`).
- Detector — runs at query time, returns finding rows.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass
class ExtractCtx:
    """Bound context an extractor receives alongside the JSONL record."""
    session_id: str
    project: str
    source_file: str
    target: str                      # "message" or "tool_call"
    tool_name: str | None = None     # set when target == "tool_call"
    tool_use_id: str | None = None
    tool_input: dict | None = None   # tool_use input args
    tool_result_text: str | None = None  # tool_result text content (if available)
    exit_code: int | None = None
    is_error: bool = False
    interrupted: bool = False


@runtime_checkable
class Extractor(Protocol):
    """Populates additional columns on messages or tool_calls during ingest.

    Lifecycle:
        registry.load_all() →  init_schema() calls fields() on every extractor
                               and runs ALTER TABLE ADD COLUMN idempotently.
        ingest reads JSONL  →  for each row, calls extract(rec, ctx) on every
                               extractor whose `targets` include the row type.
                               Returned dicts are merged into the row.
    """
    name: str
    version: str
    targets: tuple[str, ...]   # subset of {"message", "tool_call"}

    def fields(self) -> dict[str, str]:
        """Return {column_name: sqlite_type} this extractor populates.
        Use SQL type strings: "INTEGER", "TEXT", "REAL"."""
        ...

    def extract(self, rec: dict, ctx: ExtractCtx) -> dict | None:
        """Return dict of column_name -> value to write, or None to skip."""
        ...


@runtime_checkable
class Aggregator(Protocol):
    """Builds derived entity tables after raw ingest completes.

    Lifecycle:
        ingest finishes  →  for each registered aggregator, call build(conn).
                            Aggregator runs CREATE TABLE IF NOT EXISTS + UPSERT.
    """
    name: str
    version: str
    produces: tuple[str, ...]   # derived table names this aggregator writes

    def build(self, conn: sqlite3.Connection) -> None:
        """Idempotently create + populate the derived tables."""
        ...


@runtime_checkable
class Detector(Protocol):
    """Query-time pattern detector.

    Surfaced via:
      - tokenscope detectors run <name>
      - MCP run_detector tool
      - tokenscope.plugins.registry.detectors[name].run(...)
    """
    name: str
    title: str
    description: str
    params_schema: dict           # JSONSchema for params beyond standard filters
    requires: tuple[str, ...]     # extractor + aggregator names this needs

    def run(
        self,
        conn: sqlite3.Connection,
        filters: dict[str, Any],
        params: dict[str, Any],
    ) -> list[dict]:
        """Return list of finding dicts. Each row should include identifying
        keys (e.g. session_id, file_path), a `recommendation` string when a
        known anti-pattern matches, and a `result_sample` for error-class
        findings."""
        ...
