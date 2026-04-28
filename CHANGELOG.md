# Changelog

All notable changes to tokenscope are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project
uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Pluggable plugin system (`tokenscope.plugins`): `Extractor`, `Aggregator`,
  `Detector` Protocols + central `Registry`.
- 4 built-in extractors: `bash_meta`, `bash_touched_files`, `read_range`,
  `status_class`.
- 9 built-in detectors: `agent_races`, `bash_retries`, `dead_search_patterns`,
  `duplicate_reads`, `error_chains`, `paging_reads`, `permission_denials`,
  `redundant_read_ranges`, `repeat_tasks`.
- New MCP tool: `investigate` — root-cause synthesis with ranked actions.
- New CLI subcommands: `tokenscope detectors list/run`,
  `tokenscope extractors list`, `tokenscope enrich-existing`,
  `tokenscope dedupe-billing`, `tokenscope prune-ephemeral`,
  `tokenscope reparse-bash`.
- Three plugin discovery sources: built-ins, entry-points
  (`tokenscope.{extractors,aggregators,detectors}` groups), and user dir
  (`~/.config/tokenscope/plugins/`).
- Schema extensions surfaced via extractor `fields()` — idempotent
  `ALTER TABLE ADD COLUMN` for `read_offset`, `read_limit`, `touched_files`,
  `status_class`, `is_user_rejection`, `result_text_snippet`.
- Reject-aware error detection: `error_chains` and `bash_retries` now
  filter `is_user_rejection=1` by default; `include_rejections` param
  available.
- Period-over-period deltas in `get_overview`
  (`delta_today_pct`, `delta_7d_pct`, `delta_30d_pct`).
- Composite `get_insights` auto-aggregates over every registered detector
  with a headline `summary` block.
- Documentation suite under `docs/` — architecture, MCP reference, CLI
  reference, quickstart, plugin authoring guides.
- Examples under `examples/` — sample detector + extractor + demo prompts
  + transcript.
- Test suite (`pytest`) covering extractors, detectors, analytics_core,
  investigate pipeline, and MCP server.

### Fixed
- **Cost overcount**: Anthropic emits one JSONL record per content block
  (thinking/text/tool_use); each carried full request usage. Ingest now
  bills only the first record per `(session_id, request_id)`. Existing
  DBs migrated via `tokenscope dedupe-billing`.
- **Bash parser**: failed on multi-line scripts, comments, control-flow
  keywords (for/while/until/if). Rewritten with statement splitting,
  control-keyword skipping, prefix-word stripping (time/nice/nohup/...).
  NULL bash_program rate dropped from ~15% to ~0.1%.
- **Ephemeral subagent worktrees** (`/private/tmp/agent/*`) and Claude
  Code worktree dirs (`*--claude-worktrees-*`) polluted project rollups.
  Discovery now skips them or rolls them up to parent project.

## [0.1.0] - TBD

Initial public release.
