# CLI Reference

Every CLI command operates on `~/.claude/analytics.db`. Pass
`--help` to any command for full options.

## Core lifecycle

```bash
tokenscope ingest                # parse JSONL → SQLite (incremental)
tokenscope serve                 # web dashboard at http://localhost:8787
tokenscope mcp                   # MCP stdio server
```

## Reports

```bash
tokenscope report --by tool        # per-tool cost / errors / latency
tokenscope report --by task        # Agent invocations grouped by agent_type
tokenscope report --by project
tokenscope report --by session
tokenscope report --by day
tokenscope report --by file        # file hotspots
tokenscope report --by bash        # top shell commands
tokenscope report --by workflow    # tool sequence bigrams
```

Add `--limit N` to cap rows; `--project <path>` to scope.

## Plugins

```bash
tokenscope extractors list                      # registered ingest-time enrichers
tokenscope detectors list                       # registered query-time detectors
tokenscope detectors run <name>                 # run a detector
tokenscope detectors run paging_reads --param min_pages=10
tokenscope detectors run permission_denials \
    --project /Users/me/project --since 2026-04-22
```

Detectors accept the same filters as MCP (`--project`, `--session`,
`--since`, `--until`) plus per-detector params via `--param key=val`
(repeatable).

## Migrations

```bash
tokenscope dedupe-billing       # fix Anthropic 1-request-N-records double counting
tokenscope prune-ephemeral      # drop /private/tmp/agent subagent worktrees,
                                # rewrite worktree projects to parent
tokenscope reparse-bash         # re-run bash parser on existing rows
                                # (use after bash_parse.py changes)
tokenscope enrich-existing      # backfill plugin extractor columns over old rows
```

## Export

```bash
tokenscope export tool_calls --format csv -o tool_calls.csv
tokenscope export messages --format json -o messages.json
```

## Environment variables

- `TOKENSCOPE_PLUGIN_DIR` — override `~/.config/tokenscope/plugins/`.
- `CLAUDE_PROJECTS_DIR` — override `~/.claude/projects/` (testing).
- `CLAUDE_ANALYTICS_DB` — override default DB path.
