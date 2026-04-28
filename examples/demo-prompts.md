# Demo Prompts

Once `tokenscope` is registered as an MCP server in Claude Code, ask it
to introspect your own usage. These prompts each exercise different
parts of the tool surface.

## Headline scan

> *"Where am I burning tokens this week?"*

Calls `get_insights` with a `since` filter. Returns a summary block
identifying the biggest concern, plus full detector breakdown.

> *"Show me the top 5 most expensive sessions this month."*

Calls `get_top_costs(by="session", limit=5, filters={since:"30 days ago"})`.

## Root-cause investigation

> *"Investigate the worst session this week."*

Calls `investigate(target="auto", filters={since:"7 days ago"})`.
Returns ranked root causes (high/medium/low confidence) with concrete
ranked actions.

> *"Why did session a1b2c3d4-... cost so much?"*

Calls `investigate(target="session", session_id=...)`. Same shape but
scoped to that specific session.

> *"Why did session X compact?"*

Calls `find_compaction_root(session_id=...)`. Returns the largest-output
turn in the 10 minutes before each compaction.

## Pattern-specific drills

> *"Show me files I re-read repeatedly without using Grep."*

Internally `paging_reads` detector via `get_insights`. Surfaces sessions
where the agent paged through a file ≥5 times with zero Grep calls.

> *"Where am I hitting bash permission denials?"*

`permission_denials` detector. Shows sessions stuck retrying denied
commands.

> *"Find tool sequences that consistently error."*

`find_error_chains`. Reject-aware by default — user-rejected plans don't
appear.

> *"Compare cache efficiency across the models I've used."*

`get_reasoning_cache(group_by="model")`. Returns hit_ratio,
cache_creation_pct, thinking_pct_of_output per model.

## Cross-session patterns

> *"Have I been running the same task multiple times?"*

`repeat_tasks` detector via `get_insights`. Fingerprints task descriptions
and shows fingerprints invoked ≥2 times.

> *"Show me searches that never led to a Read."*

`dead_search_patterns` detector. Grep/Glob/WebSearch calls with no
follow-up Read or WebFetch.

## Per-project zoom

> *"Compare project `acme-api` spend last week vs the week before."*

Calls `get_overview(filters={project:"...", since:"7 days ago"})`. The
deltas are baked into the response.

> *"What's wasteful about the `data-pipeline` project?"*

`get_insights(filters={project:"..."})`. All detectors run scoped to
that project.

## Operational

> *"Walk me through what tokenscope can do — read the schema."*

Reads the `analytics://schema` resource. Returns markdown DDL of every
table with column types.

> *"List your detectors."*

Tool definitions for `get_insights` describe what each section means.
Or run `tokenscope detectors list` directly in a terminal.
