"""MCP stdio server exposing curated tokenscope tools to AI agents.

Run via:  tokenscope mcp
Configure in Claude Code:
  claude mcp add tokenscope /path/to/.venv/bin/tokenscope mcp
"""
from __future__ import annotations

import json
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Resource, TextContent, Tool

from . import analytics_core as core

SERVER_NAME = "tokenscope"
app: Server = Server(SERVER_NAME)


# ---------- shared filter schema fragment ----------
_FILTERS_SCHEMA = {
    "type": "object",
    "description": "Optional filters; all keys optional",
    "properties": {
        "project":    {"type": "string", "description": "Project name (use list_projects via get_top_costs)"},
        "session_id": {"type": "string"},
        "task_id":    {"type": "string", "description": "root_tool_use_id of an Agent invocation"},
        "tool":       {"type": "string", "description": "Tool name (Bash, Read, Edit, etc.)"},
        "since":      {"type": "string", "description": "ISO date YYYY-MM-DD or epoch ms"},
        "until":      {"type": "string", "description": "ISO date YYYY-MM-DD or epoch ms"},
    },
    "additionalProperties": False,
}


def _payload(name: str, args: dict[str, Any]) -> Any:
    f = args.get("filters") or {}
    if name == "get_overview":
        return core.overview(f)
    if name == "get_insights":
        return core.insights(f)
    if name == "get_top_costs":
        return core.top_costs(args.get("by", "tool"), int(args.get("limit", 20)), f)
    if name == "get_session_detail":
        sid = args.get("session_id")
        if not sid:
            return {"error": "session_id required"}
        return core.session_detail(sid)
    if name == "get_reasoning_cache":
        return core.reasoning_cache(args.get("group_by", "model"), f)
    if name == "find_duplicate_reads":
        return core.duplicate_reads(f, int(args.get("min_dups", 2)))
    if name == "find_bash_retries":
        return core.bash_retries(f, int(args.get("window_s", 60)))
    if name == "find_error_chains":
        return core.error_chains(f,
                                  int(args.get("min_n", 5)),
                                  float(args.get("min_rate", 0.2)))
    if name == "find_compaction_root":
        return core.compaction_root(args.get("session_id"), f)
    if name == "investigate":
        from .investigate import investigate as _inv
        return _inv(
            session_id=args.get("session_id"),
            target=args.get("target", "auto"),
            filters=f,
        )
    return {"error": f"unknown tool: {name}"}


@app.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="get_overview",
            description=(
                "Top-level analytics: spend (today/yesterday/7d/30d/all-time) with "
                "period-over-period delta percentages, cache hit ratio, top projects "
                "and tools, daily sparkline. Use this first to orient."
            ),
            inputSchema={
                "type": "object",
                "properties": {"filters": _FILTERS_SCHEMA},
                "additionalProperties": False,
            },
        ),
        Tool(
            name="get_insights",
            description=(
                "Composite bottleneck report. Returns 7 keys: duplicate_reads, "
                "bash_retries, error_chains, cost_outliers, compaction_root, "
                "reasoning_cache_by_model, reasoning_cache_inefficient_sessions. "
                "Use this when the user asks 'where am I burning tokens' or 'what's wasteful'."
            ),
            inputSchema={
                "type": "object",
                "properties": {"filters": _FILTERS_SCHEMA},
                "additionalProperties": False,
            },
        ),
        Tool(
            name="get_top_costs",
            description=(
                "Ranked cost breakdown along one dimension. `by` ∈ "
                "{tool, project, session, task, file, bash_program, bash_subcommand, model}. "
                "limit clamped to 100."
            ),
            inputSchema={
                "type": "object",
                "required": ["by"],
                "properties": {
                    "by": {
                        "type": "string",
                        "enum": ["tool", "project", "session", "task", "file",
                                 "bash_program", "bash_subcommand", "model"],
                    },
                    "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 20},
                    "filters": _FILTERS_SCHEMA,
                },
                "additionalProperties": False,
            },
        ),
        Tool(
            name="get_session_detail",
            description=(
                "Deep dive on a single session: timeline (per-message tokens/cost), "
                "tool breakdown, bash breakdown by program+subcommand, file activity, "
                "reasoning+cache summary."
            ),
            inputSchema={
                "type": "object",
                "required": ["session_id"],
                "properties": {"session_id": {"type": "string"}},
                "additionalProperties": False,
            },
        ),
        Tool(
            name="get_reasoning_cache",
            description=(
                "Reasoning + cache aggregations. group_by ∈ {model, session, project, day}. "
                "Returns per-key cost, hit_ratio, cache_creation_pct, thinking_pct_of_output. "
                "Use this for 'cache efficiency' or 'thinking token' questions."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "group_by": {
                        "type": "string",
                        "enum": ["model", "session", "project", "day"],
                        "default": "model",
                    },
                    "filters": _FILTERS_SCHEMA,
                },
                "additionalProperties": False,
            },
        ),
        Tool(
            name="find_duplicate_reads",
            description=(
                "Sessions where the same file was Read repeatedly with no intervening "
                "Edit/Write — wasted tokens. Returns rows of session_id+file_path+dup_reads."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "min_dups": {"type": "integer", "minimum": 1, "maximum": 50, "default": 2},
                    "filters": _FILTERS_SCHEMA,
                },
                "additionalProperties": False,
            },
        ),
        Tool(
            name="find_bash_retries",
            description=(
                "Retry loops: same bash_command repeated within window_s seconds in a "
                "session, ≥1 of which errored. Surfaces wasted bash spend."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "window_s": {"type": "integer", "minimum": 1, "maximum": 3600, "default": 60},
                    "filters": _FILTERS_SCHEMA,
                },
                "additionalProperties": False,
            },
        ),
        Tool(
            name="find_error_chains",
            description=(
                "Tool bigrams (prev_tool → next_tool) where next_tool errors at "
                "rate ≥ min_rate over ≥ min_n samples. Reveals causal patterns."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "min_n": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 5},
                    "min_rate": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.2},
                    "filters": _FILTERS_SCHEMA,
                },
                "additionalProperties": False,
            },
        ),
        Tool(
            name="find_compaction_root",
            description=(
                "For each compaction event, return the largest-output message in the "
                "10 minutes before it — likely the bloating turn that triggered compaction. "
                "Pass session_id to scope to one session."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "filters": _FILTERS_SCHEMA,
                },
                "additionalProperties": False,
            },
        ),
        Tool(
            name="investigate",
            description=(
                "Deep root-cause investigation. Gathers session facts, runs every "
                "internal detector scoped to the target, and synthesizes: "
                "{summary, facts, evidence, root_causes (with confidence), "
                "actions (ranked by impact × effort), top_expensive_turns}. "
                "Pass session_id for a specific session, or target='auto' to pick "
                "the highest-cost outlier in the filter window. Use when the user "
                "asks 'why is X expensive' or 'what's wrong with this session'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "session_id": {"type": "string"},
                    "target": {
                        "type": "string",
                        "enum": ["session", "auto", "top_concern"],
                        "default": "auto",
                    },
                    "filters": _FILTERS_SCHEMA,
                },
                "additionalProperties": False,
            },
        ),
    ]


@app.call_tool()
async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
    try:
        result = _payload(name, arguments or {})
    except Exception as e:
        result = {"error": f"{type(e).__name__}: {e}"}
    text = json.dumps(result, default=str, indent=2, ensure_ascii=False)
    return [TextContent(type="text", text=text)]


# ---------- resources ----------

@app.list_resources()
async def list_resources() -> list[Resource]:
    return [
        Resource(
            uri="analytics://schema",
            name="DB Schema",
            description="SQLite DDL for messages, tool_calls, sessions, tasks, file_activity",
            mimeType="text/markdown",
        ),
    ]


@app.read_resource()
async def read_resource(uri: str) -> str:
    s = str(uri)
    if s == "analytics://schema":
        return core.schema_markdown()
    if s.startswith("analytics://session/"):
        sid = s.rsplit("/", 1)[-1]
        return json.dumps(core.session_detail(sid), default=str, indent=2)
    raise ValueError(f"unknown resource: {uri}")


async def main() -> None:
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())
