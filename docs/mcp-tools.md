# MCP Tool Reference

10 stable named tools. Every tool accepts an optional `filters` object:
`{project, session_id, task_id, tool, since, until}`. All filters are
optional; combine freely.

---

## `get_overview`

Top-level KPIs.

**Args:** `filters`

**Returns:**
```json
{
  "spend": {
    "today": 46.55, "yesterday": 109.17, "delta_today_pct": -0.57,
    "last_7d": 930.96, "prev_7d": 944.47, "delta_7d_pct": -0.01,
    "last_30d": 2849.64, "prev_30d": 140.70, "delta_30d_pct": 19.25,
    "all_time": 1535.55
  },
  "counts": {"messages": 43427, "sessions": 135, "projects": 18},
  "tokens": {"input": ..., "output": ..., "cache_read": ..., "thinking": ...},
  "cache_hit_ratio": 0.9994,
  "health": {"compactions": 0, "api_errors": 110},
  "top_projects": [...], "top_tools": [...], "sparkline": [...]
}
```

**Use first** to orient. Always cheap.

---

## `get_insights`

Composite bottleneck report. Auto-aggregates over every registered
detector + entity-level reasoning/cache views + headline summary.

**Args:** `filters`

**Returns:** dict with sections per detector + `summary`:
```json
{
  "summary": {
    "real_errors": 23,
    "rejections": 0,
    "biggest_concern": {"detector": "error_chains", "row": {...}},
    "top_recommendation": "Validate URL or add retry-with-backoff before WebFetch"
  },
  "agent_races": [...], "bash_retries": [...], "duplicate_reads": [...],
  "error_chains": [...], "paging_reads": [...], "permission_denials": [...],
  "redundant_read_ranges": [...], "repeat_tasks": [...], "dead_search_patterns": [...],
  "cost_outliers": [...], "compaction_root": [...],
  "reasoning_cache_by_model": [...], "reasoning_cache_inefficient_sessions": [...]
}
```

Each detector section capped at 15 rows.

---

## `get_top_costs`

Ranked cost breakdown along one dimension.

**Args:** `by` (required), `limit` (default 20, max 100), `filters`

`by` ∈ `tool | project | session | task | file | bash_program | bash_subcommand | model`

**Returns:** list of `{key, cost, calls, errors, ...}` rows.

---

## `get_session_detail`

Deep dive on one session.

**Args:** `session_id` (required)

**Returns:** `{session, timeline, tool_breakdown, bash_breakdown, file_activity, reasoning_cache}`.

---

## `get_reasoning_cache`

Reasoning + cache aggregations.

**Args:** `group_by` ∈ `model | session | project | day` (default `model`), `filters`

**Returns:** rows of `{key, cost, cache_hit_ratio, cache_creation_pct, thinking_pct_of_output, ...}`.

---

## `find_duplicate_reads`

Same file Read repeatedly with no intervening Edit.

**Args:** `min_dups` (default 2), `filters`

**Returns:** rows of `{session_id, file_path, reads, dup_reads}`.

---

## `find_bash_retries`

Same bash command repeated within window with ≥1 real (non-reject) error.

**Args:** `window_s` (default 60, max 3600), `filters`

**Returns:** rows of `{session_id, bash_command, retries, real_errors, rejects, status_class_top, result_sample, recommendation}`.

---

## `find_error_chains`

Tool bigrams whose next-tool errors disproportionately. Reject-aware
by default.

**Args:** `min_n` (default 5), `min_rate` (default 0.2), `include_rejections` (default false), `filters`

**Returns:** rows of `{prev_tool, next_tool, n, real_errs, rejects, err_rate, status_class_top, result_sample, recommendation}`.

---

## `find_compaction_root`

For each compaction event, the largest-output message in the 10 minutes
before it.

**Args:** `session_id` (optional — scope to one session), `filters`

---

## `investigate`

Deep root-cause investigation. Gathers session facts, runs every
internal detector scoped to the target, and synthesizes:

**Args:**
- `session_id` — investigate this specific session
- `target` ∈ `session | auto | top_concern` (default `auto`)
- `filters` (used when `target=auto` to pick the worst session in window)

**Returns:**
```json
{
  "target": {"type": "session", "id": "...", "project": "..."},
  "summary": {
    "what": "Session ran 138 h, $103.90 cost, 3,722 messages, 2,210 tool calls.",
    "key_issue": "Agent paged through file with offset Reads instead of Grep",
    "estimated_avoidable_usd": 1.65
  },
  "facts": {...},
  "reasoning_cache": {...},
  "evidence": [...],         // ranked by $value, then count
  "root_causes": [
    {"cause": "...", "confidence": "high|medium|low", "evidence_signals": [...], "estimated_impact_usd": 1.65}
  ],
  "actions": [
    {"label": "...", "action": "...", "impact": "high|medium|low", "effort": "behavioral|config|code"}
  ],
  "top_expensive_turns": [...]
}
```

Use when the user asks *"why is X expensive"*, *"what's wrong with this session"*, or *"investigate the worst this week"*.

---

## Resources

`analytics://schema` — markdown rendering of the SQLite DDL. Use to plan
ad-hoc analyses.

`analytics://session/{session_id}` — JSON `get_session_detail` payload as a resource.
