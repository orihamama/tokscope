"""Materialize sessions / tasks / file_activity / tool_sequences after ingest."""
from __future__ import annotations
import sqlite3

from .bash_parse import parse_bash
from .db import bump_etag, connect, init_schema, transaction


def rebuild_sessions(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM sessions")
    conn.execute(
        """INSERT INTO sessions(
                session_id, project, cwd, git_branch,
                started_at, ended_at,
                total_cost_usd, total_input, total_output,
                total_cache_read, total_cache_creation, total_thinking_tokens,
                message_count, compaction_count, error_count, cache_hit_ratio)
           SELECT
                session_id,
                MAX(project), MAX(cwd), MAX(git_branch),
                MIN(timestamp), MAX(timestamp),
                COALESCE(SUM(cost_usd),0),
                COALESCE(SUM(input_tokens),0), COALESCE(SUM(output_tokens),0),
                COALESCE(SUM(cache_read),0), COALESCE(SUM(cache_creation),0),
                COALESCE(SUM(thinking_tokens),0),
                COUNT(*),
                COALESCE(SUM(is_compact_summary),0),
                COALESCE(SUM(is_api_error),0),
                CASE
                    WHEN COALESCE(SUM(cache_read),0) + COALESCE(SUM(input_tokens),0) > 0
                    THEN CAST(SUM(cache_read) AS REAL) /
                         (SUM(cache_read) + SUM(input_tokens))
                    ELSE NULL
                END
           FROM messages
           WHERE session_id IS NOT NULL
           GROUP BY session_id"""
    )
    # patch tool_call_count
    conn.execute(
        """UPDATE sessions SET tool_call_count = (
              SELECT COUNT(*) FROM tool_calls WHERE tool_calls.session_id = sessions.session_id
           )"""
    )


def rebuild_tasks(conn: sqlite3.Connection) -> None:
    """Build tasks from sub-agent chains.

    A 'task' = one Agent tool_use invocation. Linkage:
      - parent has tool_calls.tool_name='Agent' with agent_id captured from
        toolUseResult.agentId
      - subagent jsonl file 'agent-<agentId>.jsonl' contributes messages/tool_calls
        with messages.agent_id = agentId
    Aggregate descendants (messages + tool_calls) by agent_id JOIN.
    """
    conn.execute("DELETE FROM tasks")
    conn.execute(
        """INSERT INTO tasks(
                root_tool_use_id, session_id, project, agent_type, description,
                started_at, total_input, total_output, total_cache_read,
                total_cache_creation, total_cost_usd, message_count,
                tool_call_count, is_error)
           SELECT
                tc.id,
                tc.session_id, tc.project,
                COALESCE(tc.agent_subtype,'default'),
                tc.agent_description,
                tc.timestamp,
                COALESCE(tc.attributed_input_tokens,0),
                COALESCE(tc.attributed_output_tokens,0),
                COALESCE(tc.attributed_cache_read,0),
                COALESCE(tc.attributed_cache_creation,0),
                COALESCE(tc.attributed_cost_usd,0),
                0, 0,
                COALESCE(tc.is_error,0)
           FROM tool_calls tc
           WHERE tc.tool_name='Agent'"""
    )

    # Aggregate descendant messages + tool_calls by agent_id JOIN
    conn.execute(
        """UPDATE tasks SET
              total_input = total_input + COALESCE((
                  SELECT SUM(input_tokens) FROM messages
                  WHERE agent_id = (SELECT agent_id FROM tool_calls WHERE id=tasks.root_tool_use_id)
              ),0),
              total_output = total_output + COALESCE((
                  SELECT SUM(output_tokens) FROM messages
                  WHERE agent_id = (SELECT agent_id FROM tool_calls WHERE id=tasks.root_tool_use_id)
              ),0),
              total_cache_read = total_cache_read + COALESCE((
                  SELECT SUM(cache_read) FROM messages
                  WHERE agent_id = (SELECT agent_id FROM tool_calls WHERE id=tasks.root_tool_use_id)
              ),0),
              total_cache_creation = total_cache_creation + COALESCE((
                  SELECT SUM(cache_creation) FROM messages
                  WHERE agent_id = (SELECT agent_id FROM tool_calls WHERE id=tasks.root_tool_use_id)
              ),0),
              total_cost_usd = total_cost_usd + COALESCE((
                  SELECT SUM(cost_usd) FROM messages
                  WHERE agent_id = (SELECT agent_id FROM tool_calls WHERE id=tasks.root_tool_use_id)
              ),0),
              message_count = COALESCE((
                  SELECT COUNT(*) FROM messages
                  WHERE agent_id = (SELECT agent_id FROM tool_calls WHERE id=tasks.root_tool_use_id)
              ),0),
              tool_call_count = COALESCE((
                  SELECT COUNT(*) FROM tool_calls tc2
                  JOIN messages m2 ON tc2.message_uuid = m2.uuid
                  WHERE m2.agent_id = (SELECT agent_id FROM tool_calls WHERE id=tasks.root_tool_use_id)
              ),0),
              ended_at = COALESCE((
                  SELECT MAX(timestamp) FROM messages
                  WHERE agent_id = (SELECT agent_id FROM tool_calls WHERE id=tasks.root_tool_use_id)
              ), started_at)
           WHERE EXISTS (
              SELECT 1 FROM tool_calls WHERE id=tasks.root_tool_use_id AND agent_id IS NOT NULL
           )"""
    )

    # Compute duration
    conn.execute(
        """UPDATE tasks SET duration_ms = ended_at - started_at
           WHERE ended_at IS NOT NULL AND started_at IS NOT NULL"""
    )


def rebuild_file_activity(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM file_activity")
    conn.execute(
        """INSERT INTO file_activity(session_id, project, file_path,
                  reads, edits, writes, total_cost_usd)
           SELECT
                session_id, project, file_path,
                SUM(CASE WHEN tool_name='Read' THEN 1 ELSE 0 END),
                SUM(CASE WHEN tool_name='Edit' THEN 1 ELSE 0 END),
                SUM(CASE WHEN tool_name='Write' THEN 1 ELSE 0 END),
                SUM(attributed_cost_usd)
           FROM tool_calls
           WHERE file_path IS NOT NULL
             AND tool_name IN ('Read','Edit','Write','MultiEdit')
           GROUP BY session_id, project, file_path"""
    )


def rebuild_tool_sequences(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM tool_sequences")
    conn.execute(
        """INSERT INTO tool_sequences(project, prev_tool, next_tool, count)
           SELECT project, prev_tool, next_tool, COUNT(*) FROM (
               SELECT
                   project,
                   tool_name AS next_tool,
                   LAG(tool_name) OVER (
                       PARTITION BY session_id ORDER BY timestamp, id
                   ) AS prev_tool
               FROM tool_calls
               WHERE timestamp IS NOT NULL
           )
           WHERE prev_tool IS NOT NULL
           GROUP BY project, prev_tool, next_tool"""
    )


def backfill_bash(conn: sqlite3.Connection) -> int:
    """Populate bash_program/subcommand/category/pipe_count/has_sudo for legacy rows."""
    rows = conn.execute(
        "SELECT id, bash_command FROM tool_calls "
        "WHERE tool_name='Bash' AND bash_program IS NULL "
        "  AND bash_command IS NOT NULL"
    ).fetchall()
    n = 0
    for r in rows:
        p = parse_bash(r["bash_command"])
        conn.execute(
            "UPDATE tool_calls SET bash_program=?, bash_subcommand=?, "
            "bash_category=?, bash_pipe_count=?, bash_has_sudo=? WHERE id=?",
            (p["program"], p["subcommand"], p["category"],
             p["pipe_count"], p["has_sudo"], r["id"]),
        )
        n += 1
    return n


def rebuild_all() -> dict:
    conn = connect()
    init_schema(conn)
    out = {}
    with transaction(conn):
        rebuild_sessions(conn)
        out["sessions"] = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        rebuild_tasks(conn)
        out["tasks"] = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        rebuild_file_activity(conn)
        out["file_activity"] = conn.execute("SELECT COUNT(*) FROM file_activity").fetchone()[0]
        rebuild_tool_sequences(conn)
        out["tool_sequences"] = conn.execute("SELECT COUNT(*) FROM tool_sequences").fetchone()[0]
        out["bash_backfilled"] = backfill_bash(conn)
        bump_etag(conn)
    return out
