# Writing an Extractor

An extractor is a plugin that runs at ingest time, receives a JSONL
record + bound context, and returns a dict of column values to write
onto the row. Extractors declare which columns they own; tokenscope
adds those columns idempotently at `init_schema` time.

## Protocol

```python
class Extractor(Protocol):
    name: str
    version: str
    targets: tuple[str, ...]   # subset of {"message", "tool_call"}

    def fields(self) -> dict[str, str]:
        """Return {column_name: sqlite_type}."""

    def extract(self, rec: dict, ctx: ExtractCtx) -> dict | None:
        """Return {column_name: value} to write, or None to skip."""
```

`ExtractCtx` carries `session_id`, `project`, `source_file`, `target`,
`tool_name`, `tool_use_id`, `tool_input`, `tool_result_text`,
`exit_code`, `is_error`, `interrupted`. Use these instead of re-walking
the JSONL.

## Example — `webfetch_host`

Capture the host portion of WebFetch URLs into a new column for
per-host analytics.

```python
# my_pkg/webfetch_host.py
from urllib.parse import urlparse

from tokenscope.plugins import registry


class WebFetchHost:
    name = "webfetch_host"
    version = "1"
    targets = ("tool_call",)

    def fields(self):
        return {"webfetch_host": "TEXT"}

    def extract(self, rec, ctx):
        if ctx.tool_name != "WebFetch":
            return None
        url = (ctx.tool_input or {}).get("url")
        if not url:
            return None
        try:
            host = urlparse(url).hostname
        except Exception:
            return None
        if not host:
            return None
        return {"webfetch_host": host}


registry.register_extractor(WebFetchHost())
```

## Registering

- **User dir**: `~/.config/tokenscope/plugins/webfetch_host.py` — auto-import on startup.
- **Entry point**: `[project.entry-points."tokenscope.extractors"]`.
- **Built-in**: add under `src/tokenscope/plugins/builtins/extractors/` and import in `__init__.py`.

## Backfilling

Extractors only run on **new** ingestions by default. To populate the
new columns over existing rows:

```bash
tokenscope enrich-existing
```

This walks every JSONL referenced by the DB and re-invokes every
registered extractor.

## Lifecycle

1. `tokenscope.db.init_schema(conn)` runs `_apply_extractor_schema`,
   which iterates registered extractors and runs `ALTER TABLE
   {messages|tool_calls} ADD COLUMN {name} {type}` for any missing
   columns. Idempotent.
2. During `tokenscope ingest`, the parser invokes every registered
   extractor on each row, merges returned dicts, and updates the row.
3. `version` field bumps trigger backfill (planned: tokenscope tracks
   `meta.extractor_versions` and prompts re-run when stale).

## Tips

- Keep extractors fast — they run per JSONL record and per result.
- Return `None` to skip rows your extractor doesn't apply to. Don't
  return columns with `None` values you didn't intend to clear.
- Use `targets=("tool_call",)` for tool-related logic; `("message",)`
  for assistant/user message-level fields. Most extractors target
  `tool_call`.
- If you need data only available in tool_result (status, exit code,
  result text), tokenscope invokes your extractor a second time after
  the result lands so you can refine columns then.
