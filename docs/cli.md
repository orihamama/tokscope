# CLI Reference

Every CLI command operates on `~/.claude/analytics.db`. Pass
`--help` to any command for full options.

## Core lifecycle

```bash
tokscope ingest                # parse JSONL → SQLite (incremental)
tokscope serve                 # web dashboard at http://localhost:8787
tokscope mcp                   # MCP stdio server
```

## Reports

```bash
tokscope report --by tool        # per-tool cost / errors / latency
tokscope report --by task        # Agent invocations grouped by agent_type
tokscope report --by project
tokscope report --by session
tokscope report --by day
tokscope report --by file        # file hotspots
tokscope report --by bash        # top shell commands
tokscope report --by workflow    # tool sequence bigrams
```

Add `--limit N` to cap rows; `--project <path>` to scope.

## Plugins

```bash
tokscope extractors list                      # registered ingest-time enrichers
tokscope detectors list                       # registered query-time detectors
tokscope detectors run <name>                 # run a detector
tokscope detectors run paging_reads --param min_pages=10
tokscope detectors run permission_denials \
    --project /Users/me/project --since 2026-04-22
```

Detectors accept the same filters as MCP (`--project`, `--session`,
`--since`, `--until`) plus per-detector params via `--param key=val`
(repeatable).

## Migrations

```bash
tokscope dedupe-billing       # fix Anthropic 1-request-N-records double counting
tokscope prune-ephemeral      # drop /private/tmp/agent subagent worktrees,
                                # rewrite worktree projects to parent
tokscope reparse-bash         # re-run bash parser on existing rows
                                # (use after bash_parse.py changes)
tokscope enrich-existing      # backfill plugin extractor columns over old rows
```

## Export

```bash
tokscope export tool_calls --format csv -o tool_calls.csv
tokscope export messages --format json -o messages.json
```

## Environment variables

- `TOKSCOPE_PLUGIN_DIR` — override `~/.config/tokscope/plugins/`.
- `CLAUDE_PROJECTS_DIR` — override `~/.claude/projects/` (testing).
- `CLAUDE_ANALYTICS_DB` — override default DB path.
