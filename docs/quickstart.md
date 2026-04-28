# Quickstart

5 minutes from install to actionable insights.

## Install

### Easiest — uvx, no local install

```bash
claude mcp add tokscope -- uvx tokscope mcp
```

### From source

```bash
git clone https://github.com/orihamama/tokscope
cd tokscope
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

Verify:

```bash
.venv/bin/tokscope --help
.venv/bin/tokscope detectors list
```

## First run

```bash
# Parse all your Claude Code session logs into ~/.claude/analytics.db
.venv/bin/tokscope ingest

# Quick CLI report
.venv/bin/tokscope report --by tool

# Open the web dashboard at http://localhost:8787
.venv/bin/tokscope serve
```

## Register the MCP server with Claude Code

```bash
claude mcp add tokscope -- /full/path/to/.venv/bin/tokscope mcp
claude mcp list                    # ✓ Connected
```

Open a new Claude Code session. The 10 tools auto-load.

## Ask Claude

Try these prompts in any session:

- **"Where am I burning tokens this week?"**
  → calls `get_insights` with `since` filter; returns headline summary + all detector findings.

- **"Investigate why session d8d4009f cost so much"**
  → calls `investigate(session_id=...)`; returns ranked root causes + actions.

- **"Find paged reads with no Grep, top 10"**
  → calls `find_paging_reads` (or `get_insights` for the paging_reads section).

- **"Compare cache efficiency across models"**
  → calls `get_reasoning_cache(group_by="model")`.

- **"Top 5 most expensive bash subcommands this week"**
  → calls `get_top_costs(by="bash_subcommand", limit=5, filters={since:"..."})`.

- **"Why did session X compact?"**
  → calls `find_compaction_root(session_id="X")`.

## Verify migration fixes (one-time)

If your DB has data from before the v0.1 fixes:

```bash
# Anthropic emits 1 API request as N JSONL records — fix double billing
.venv/bin/tokscope dedupe-billing

# Subagent ephemeral worktrees (/private/tmp/agent/*) pollute project rollups
.venv/bin/tokscope prune-ephemeral

# Backfill plugin extractor columns (status_class, read_offset, touched_files...)
.venv/bin/tokscope enrich-existing
```

## Power-user: run individual detectors

```bash
.venv/bin/tokscope detectors list
.venv/bin/tokscope detectors run paging_reads --param min_pages=10
.venv/bin/tokscope detectors run permission_denials --since 2026-04-22
.venv/bin/tokscope detectors run redundant_read_ranges --param min_redundancy=3
```

## Author your own detector

Drop a file in `~/.config/tokscope/plugins/` — see [`writing-a-detector.md`](writing-a-detector.md). It's auto-discovered, exposed in CLI, and flows through `get_insights` automatically.

## Troubleshooting

- **MCP tools don't appear in Claude Code**: open a new session — tools load at session start, not mid-conversation.
- **"all-time spend looks 2× higher than expected"**: run `dedupe-billing`. Anthropic JSONL emits multiple records per API request; tokscope's ingest now bills only the first, but old DBs need migration.
- **`get_insights` payload too big**: filter to a project or time window. Each detector section is capped at 15 rows.
- **Empty detectors**: confirm your DB is populated (`tokscope report --by tool` shows rows). Then check `tokscope extractors list` — the columns each detector requires must exist.
