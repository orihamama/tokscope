# Architecture

tokscope = three layers + three plugin types + three surfaces.

```
                    ┌─────────────────────┐
                    │   JSONL session     │
                    │   logs (~/.claude)  │
                    └──────────┬──────────┘
                               │
                               ▼
        ┌──────────────────────────────────────────────┐
        │  Extractors (per-record)                     │  ← pluggable
        │  bash_meta · bash_touched_files · read_range │
        │  status_class                                │
        └──────────────────────┬───────────────────────┘
                               │
                               ▼
                  ┌─────────────────────────────┐
                  │  SQLite entity tables       │
                  │  messages · tool_calls      │
                  │  sessions · tasks           │
                  │  file_activity · meta       │
                  └──────────────┬──────────────┘
                                 │
                                 ▼
        ┌──────────────────────────────────────────────┐
        │  Detectors (query-time pattern detection)    │  ← pluggable
        │  9 built-ins: paging_reads, error_chains,    │
        │  permission_denials, redundant_read_ranges,  │
        │  agent_races, bash_retries, duplicate_reads, │
        │  repeat_tasks, dead_search_patterns          │
        └──────────────────────┬───────────────────────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
        ┌──────────┐    ┌──────────┐    ┌──────────────┐
        │   CLI    │    │  FastAPI │    │  MCP server  │
        │ tokens-  │    │  (web    │    │  (stdio,     │
        │  cope    │    │   dash)  │    │   10 tools)  │
        └──────────┘    └──────────┘    └──────────────┘
```

## Entities

| Entity | Identifier | Storage | Notes |
|---|---|---|---|
| Project | path string | derived | from `messages.project` |
| Session | `session_id` | `sessions` | aggregated from messages + tool_calls |
| Task | `root_tool_use_id` | `tasks` | one per Agent invocation |
| Turn | message uuid | `messages` | one per JSONL record (multiple per API request) |
| ApiRequest | `(session_id, request_id)` | conceptual | only the first turn per request bills (dedupe applied) |
| ToolCall | `tool_use_id` | `tool_calls` | one per tool invocation |
| FileAccess | `(session_id, file_path)` | derived | aggregated reads/edits/writes/bash touches |

## Plugin types

### Extractor

Runs at ingest time. Receives one JSONL record + bound context. Returns
`{column: value}` dict to write onto the row. Declares which columns it
owns via `fields()`. Schema additions are idempotent (`ALTER TABLE ADD
COLUMN IF NOT EXISTS`).

See [`docs/writing-an-extractor.md`](writing-an-extractor.md).

### Aggregator (planned)

Will build derived entity tables (`api_requests`, `file_accesses`, etc.)
after raw ingest. Currently unused — the entity model handles its needs
via tool_calls + messages joins.

### Detector

Runs at query time. Returns finding rows. Declares its `params_schema`,
`requires` (extractor names it depends on), and `description` (surfaces
in `tokscope detectors list`).

See [`docs/writing-a-detector.md`](writing-a-detector.md).

## Discovery

Plugins are registered three ways, in this order:

1. **Built-ins**: imported at module load via `tokscope/plugins/builtins/`.
2. **Entry points**: third-party packages declare in pyproject:
   ```toml
   [project.entry-points."tokscope.detectors"]
   my_detector = "my_pkg.module:MyDetector"
   ```
3. **User dir**: `*.py` files in `~/.config/tokscope/plugins/` are
   auto-imported. Override with `TOKSCOPE_PLUGIN_DIR=/path` env var.

## Surfaces

### MCP (10 named tools, stable surface)

`get_overview`, `get_insights`, `get_top_costs`, `get_session_detail`,
`get_reasoning_cache`, `find_duplicate_reads`, `find_bash_retries`,
`find_error_chains`, `find_compaction_root`, `investigate`.

`get_insights` automatically aggregates over every registered detector
+ entity-level reasoning/cache views + a headline summary picking the
biggest concern. Plugin-added detectors flow through automatically — no
MCP code change needed.

### CLI

Entity-level reports (`tokscope report --by tool|task|session|...`),
plus power-user plugin tooling: `tokscope detectors list/run`,
`tokscope extractors list`, `tokscope enrich-existing`,
`tokscope dedupe-billing`, `tokscope prune-ephemeral`.

### Web dashboard

`tokscope serve` at `http://localhost:8787` — auto-reload on JSONL
changes via watchdog filesystem events.

## Privacy

- Reads only local files under `~/.claude/projects/`
- Writes only to `~/.claude/analytics.db` (SQLite, single file)
- No network calls except optional weekly LiteLLM pricing refresh
- No telemetry, no phone-home
