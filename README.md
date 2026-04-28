# tokscope

[![PyPI](https://img.shields.io/pypi/v/tokscope.svg)](https://pypi.org/project/tokscope/)
[![Python](https://img.shields.io/pypi/pyversions/tokscope.svg)](https://pypi.org/project/tokscope/)
[![CI](https://github.com/orihamama/tokscope/actions/workflows/ci.yml/badge.svg)](https://github.com/orihamama/tokscope/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![MCP](https://img.shields.io/badge/MCP-compatible-blue)](https://modelcontextprotocol.io)

Token-spend analytics for Claude Code with **per-tool**, **per-task**, and **per-project** breakdown — the dimensions ccusage and other OSS tools don't ship.

Three surfaces, one DB:
- **MCP server** — Claude Code introspects its own spend in-session
- **CLI** — fast terminal reports + scriptable exports
- **Web dashboard** — `http://localhost:8787`, auto-refresh on file change

Reads `~/.claude/projects/**/*.jsonl` directly. Builds a SQLite DB at `~/.claude/analytics.db`. No network egress, no telemetry.

---

## Install

### MCP (recommended)

Zero-install via `uvx`:

```bash
# Claude Code
claude mcp add tokscope -- uvx tokscope mcp
```

Or in `~/.claude.json` / project `.mcp.json`:

```json
{
  "mcpServers": {
    "tokscope": {
      "command": "uvx",
      "args": ["tokscope", "mcp"]
    }
  }
}
```

Claude Desktop (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "tokscope": {
      "command": "uvx",
      "args": ["tokscope", "mcp"]
    }
  }
}
```

Then ask Claude Code: *"where am I burning tokens today?"* → it calls `get_insights`.

### CLI / dashboard (pipx)

```bash
pipx install tokscope
tokscope ingest          # parse JSONL → SQLite (incremental)
tokscope serve           # dashboard at http://localhost:8787
tokscope report --by tool
```

### Homebrew

```bash
brew tap orihamama/tokscope
brew install tokscope
```

### From source

```bash
git clone https://github.com/orihamama/tokscope
cd tokscope
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

---

## MCP tools

| Tool | Purpose |
|---|---|
| `get_overview` | Today/7d/30d/all-time spend, deltas, cache hit, top projects + tools, sparkline |
| `get_insights` | Composite bottleneck report — duplicate reads, retries, error chains, outliers |
| `get_top_costs` | Ranked breakdown by tool / project / session / task / file / bash / model |
| `get_session_detail` | Deep dive on one session — timeline, tools, files, reasoning |
| `get_reasoning_cache` | Cache + thinking-token aggregations grouped by model / session / project / day |
| `find_duplicate_reads` | Files re-read with no intervening edit |
| `find_bash_retries` | Same bash command retried within window — wasted spend |
| `find_error_chains` | Tool bigrams where the next tool errors disproportionately |
| `find_compaction_root` | Largest-output message in 10 min before each compaction |

All filterable by project / session / task / tool / time range. Resources: `analytics://schema`, `analytics://session/{session_id}`.

---

## CLI

```bash
tokscope ingest                       # parse JSONL → SQLite (incremental)
tokscope report --by tool             # per-tool spend, errors, latency
tokscope report --by task             # sub-agent invocations
tokscope report --by project
tokscope report --by session
tokscope report --by day
tokscope report --by file             # file hotspots
tokscope report --by bash             # top shell commands
tokscope report --by workflow         # tool sequence bigrams

tokscope serve                        # dashboard at http://localhost:8787 (auto-watch)
tokscope export tool_calls --format csv -o tool_calls.csv
tokscope mcp                          # MCP stdio server
```

---

## Dashboard tabs

| Tab | Insight |
|-----|---------|
| Overview | Today / 7d / 30d / all-time spend, cache hit ratio, sparkline, top projects + tools |
| Tools | Cost ranking, error rate, latency, payload size, interrupted/truncated/user-modified |
| Tasks | Sub-agent invocations grouped by type, top expensive tasks with description |
| Projects | Project leaderboard, click → sessions filter |
| Sessions | Session list with compactions, errors, cache hit, cost |
| Files | File hotspots (reads/edits/writes per file), re-read warnings |
| Bash | Top commands, exit codes, sandbox-bypass count, background tasks |
| Search | Top Grep patterns, Glob paths, WebFetch URLs, WebSearch queries |
| Workflow | Tool sequence bigrams (Read→Edit, Bash→Read, etc), permission-mode share |
| Health | API errors over time, sessions with most compactions, error tools, long sessions |
| Heatmap | Spend by day-of-week × hour-of-day |
| Ledger | Daily spend, by-model breakdown, CSV/JSON export |

---

## How it works

1. **Discovery** — scans `~/.claude/projects/<slug>/*.jsonl` and `<slug>/<sid>/subagents/*.jsonl`
2. **Incremental parsing** — tracks `mtime + size + last_offset` per file in `file_state` table; resumes after partial-line writes
3. **Turn assembly** — pairs assistant messages with following user `tool_result` blocks
4. **Token attribution** — output tokens proportionally assigned to tool_use blocks by input byte-size; input tokens of the next assistant turn proportionally assigned back to the just-closed tool_calls by result byte-size
5. **Pricing** — LiteLLM model JSON cached locally, weekly refresh; fuzzy match on opus/sonnet/haiku families; hardcoded fallback for Claude 4.x
6. **Sub-agent linkage** — Agent tool_calls capture `agentId` from `toolUseResult`; subagent JSONL files write `agent_id` on each message; aggregation joins on `agent_id`
7. **Watcher** — `watchdog` filesystem events with 5s debounce → incremental re-ingest → frontend ETag-driven refresh

---

## Privacy

- Reads only local files under `~/.claude/projects/`
- Writes only to `~/.claude/analytics.db`
- No network calls except optional weekly LiteLLM pricing refresh (HTTPS)
- No telemetry, no analytics, no phone-home

---

## Documentation

- [Quickstart](docs/quickstart.md) — install + first run + sample prompts.
- [Architecture](docs/architecture.md) — entity model + plugin layers + discovery.
- [MCP tool reference](docs/mcp-tools.md) — all 10 tools with input/output shapes.
- [CLI reference](docs/cli.md) — every subcommand.
- [Writing a detector](docs/writing-a-detector.md) — pluggable query-time pattern.
- [Writing an extractor](docs/writing-an-extractor.md) — pluggable ingest enrichment.

## Examples

- [Demo prompts](examples/demo-prompts.md) — what to ask Claude once MCP is registered.
- [Live transcript](examples/transcript.md) — feel of an end-to-end session.
- [Sample detector](examples/sample-detector.py) — drop-in plugin file.
- [Sample extractor](examples/sample-extractor.py) — drop-in extractor file.

## Contributing

PRs welcome. Bug reports + feature ideas → [Issues](https://github.com/orihamama/tokscope/issues).

Run tests:

```bash
.venv/bin/python -m pytest tests/ -v
```

## License

[MIT](LICENSE) © 2026 Ori Hamama

---

*Not affiliated with or endorsed by Anthropic. Claude, Claude Code, and Model Context Protocol are trademarks of Anthropic, PBC.*
