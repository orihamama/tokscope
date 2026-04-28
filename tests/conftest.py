"""Shared fixtures: synthetic SQLite DB with deterministic test data."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from tokenscope.db import init_schema


def _row_dict(conn: sqlite3.Connection, table: str, **fields) -> None:
    cols = list(fields.keys())
    placeholders = ",".join("?" * len(cols))
    conn.execute(
        f"INSERT INTO {table}({','.join(cols)}) VALUES({placeholders})",
        list(fields.values()),
    )


def _seed(conn: sqlite3.Connection) -> None:
    """Insert a small but representative dataset:
    - 2 sessions across 2 projects
    - 5 messages, one with is_compact_summary=1
    - 11 tool_calls covering Bash (success + denied + retry + touched_files),
      Read (paged 5x same file), Edit, Agent (busy), WebFetch (404)
    """
    # Sessions
    _row_dict(
        conn,
        "sessions",
        session_id="S1",
        project="/p/proj-a",
        started_at=1700000000000,
        ended_at=1700001000000,
        message_count=10,
        tool_call_count=8,
        compaction_count=0,
        error_count=3,
        total_cost_usd=5.0,
        cache_hit_ratio=0.95,
    )
    _row_dict(
        conn,
        "sessions",
        session_id="S2",
        project="/p/proj-b",
        started_at=1700100000000,
        ended_at=1700200000000,
        message_count=100,
        tool_call_count=50,
        compaction_count=1,
        error_count=1,
        total_cost_usd=25.0,
        cache_hit_ratio=0.90,
    )

    # Messages
    msgs = [
        (
            "M1a",
            "R1",
            "S1",
            "/p/proj-a",
            1700000000000,
            "assistant",
            "opus",
            100,
            200,
            500,
            1000,
            10,
            1.0,
            0,
            0,
        ),
        (
            "M1b",
            "R1",
            "S1",
            "/p/proj-a",
            1700000001000,
            "assistant",
            "opus",
            0,
            0,
            0,
            0,
            0,
            0.0,
            0,
            0,
        ),
        (
            "M2",
            "R2",
            "S1",
            "/p/proj-a",
            1700000060000,
            "assistant",
            "opus",
            50,
            100,
            200,
            5000,
            0,
            0.5,
            0,
            0,
        ),
        (
            "M3",
            "R3",
            "S2",
            "/p/proj-b",
            1700100000000,
            "assistant",
            "opus",
            170000,
            5000,
            140000,
            500,
            0,
            8.0,
            0,
            0,
        ),
        (
            "M4",
            "R4",
            "S2",
            "/p/proj-b",
            1700100600000,
            "assistant",
            "opus",
            100,
            200,
            300,
            200000,
            0,
            12.0,
            1,
            0,
        ),
    ]
    for r in msgs:
        _row_dict(
            conn,
            "messages",
            uuid=r[0],
            request_id=r[1],
            session_id=r[2],
            project=r[3],
            timestamp=r[4],
            role=r[5],
            model=r[6],
            input_tokens=r[7],
            output_tokens=r[8],
            cache_creation=r[9],
            cache_read=r[10],
            thinking_tokens=r[11],
            cost_usd=r[12],
            is_compact_summary=r[13],
            is_api_error=r[14],
            source_file="/tmp/seed.jsonl",
        )

    def tc(**k):
        defaults = dict(
            duration_ms=100,
            result_bytes=200,
            result_lines=None,
            result_total_tokens=None,
            is_error=0,
            interrupted=0,
            user_modified=0,
            truncated=0,
            exit_code=None,
            attributed_input_tokens=0,
            attributed_output_tokens=0,
            attributed_cache_creation=0,
            attributed_cache_read=0,
            parent_tool_use_id=None,
            bash_command=None,
            bash_background=0,
            bash_sandbox_disabled=0,
            file_path=None,
            search_pattern=None,
            agent_subtype=None,
            agent_description=None,
            web_url=None,
            web_query=None,
            agent_id=None,
            bash_program=None,
            bash_subcommand=None,
            bash_category=None,
            bash_pipe_count=0,
            bash_has_sudo=0,
            read_offset=None,
            read_limit=None,
            touched_files=None,
            status_class=None,
            is_user_rejection=0,
            result_text_snippet=None,
        )
        defaults.update(k)
        _row_dict(conn, "tool_calls", **defaults)

    # Two denied bash retries (same command)
    tc(
        id="T1",
        message_uuid="M1a",
        tool_name="Bash",
        session_id="S1",
        project="/p/proj-a",
        timestamp=1700000000100,
        attributed_cost_usd=0.1,
        attributed_output_tokens=50,
        bash_command="git status",
        bash_program="git",
        bash_subcommand="status",
        bash_category="vcs",
        is_error=1,
        status_class="denied",
        result_text_snippet="Permission to use Bash has been denied",
    )
    tc(
        id="T2",
        message_uuid="M1a",
        tool_name="Bash",
        session_id="S1",
        project="/p/proj-a",
        timestamp=1700000000300,
        attributed_cost_usd=0.1,
        attributed_output_tokens=50,
        bash_command="git status",
        bash_program="git",
        bash_subcommand="status",
        bash_category="vcs",
        is_error=1,
        status_class="denied",
        result_text_snippet="Permission to use Bash has been denied",
    )
    tc(
        id="T2b",
        message_uuid="M1a",
        tool_name="Bash",
        session_id="S1",
        project="/p/proj-a",
        timestamp=1700000000400,
        attributed_cost_usd=0.1,
        attributed_output_tokens=50,
        bash_command="git status",
        bash_program="git",
        bash_subcommand="status",
        bash_category="vcs",
        is_error=1,
        status_class="denied",
        result_text_snippet="Permission to use Bash has been denied",
    )
    # 5 paged Reads of /repo/big.cpp, no Grep in this session
    for i in range(5):
        tc(
            id=f"T{3 + i}",
            message_uuid="M1a",
            tool_name="Read",
            session_id="S1",
            project="/p/proj-a",
            timestamp=1700000000500 + i * 200,
            attributed_cost_usd=0.05,
            attributed_output_tokens=80,
            file_path="/repo/big.cpp",
            read_offset=1 + i * 200,
            read_limit=200,
            result_lines=200,
            result_bytes=3000,
            status_class="success",
        )
    # WebFetch 404
    tc(
        id="T8",
        message_uuid="M2",
        tool_name="WebFetch",
        session_id="S1",
        project="/p/proj-a",
        timestamp=1700000060500,
        attributed_cost_usd=0.05,
        attributed_output_tokens=80,
        web_url="https://example.com/x",
        is_error=1,
        status_class="http_error",
        result_text_snippet="Request failed with status code 404",
    )
    # Bash mv (touched files JSON)
    tc(
        id="T9",
        message_uuid="M3",
        tool_name="Bash",
        session_id="S2",
        project="/p/proj-b",
        timestamp=1700100000500,
        attributed_cost_usd=0.5,
        attributed_output_tokens=200,
        bash_command="mv a.txt b.txt",
        bash_program="mv",
        bash_category="file_ops",
        touched_files='["b.txt"]',
        status_class="success",
    )
    # Agent busy
    tc(
        id="T10",
        message_uuid="M3",
        tool_name="Agent",
        session_id="S2",
        project="/p/proj-b",
        timestamp=1700100001000,
        attributed_cost_usd=0.5,
        attributed_output_tokens=200,
        agent_subtype="general-purpose",
        agent_description="Scan for X",
        agent_id="agentX",
        is_error=1,
        status_class="agent_busy",
        result_text_snippet="Cannot resume agent: it is still running. Use TaskStop",
    )
    # Edit
    tc(
        id="T11",
        message_uuid="M3",
        tool_name="Edit",
        session_id="S2",
        project="/p/proj-b",
        timestamp=1700100001500,
        attributed_cost_usd=0.05,
        attributed_output_tokens=80,
        file_path="/repo/big.cpp",
        status_class="success",
    )
    conn.commit()


@pytest.fixture
def seeded_db(tmp_path: Path) -> sqlite3.Connection:
    """Fresh on-disk SQLite seeded with a deterministic dataset."""
    db = tmp_path / "test.db"
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    init_schema(conn)
    _seed(conn)
    return conn


@pytest.fixture
def patched_core(seeded_db, monkeypatch):
    """Make tokenscope.analytics_core._conn return the seeded DB so the
    public functions (overview, top_costs, session_detail, etc.) operate
    on test data instead of the user's real ~/.claude/analytics.db.
    """
    from tokenscope import analytics_core as core

    monkeypatch.setattr(core, "_conn", lambda: seeded_db)
    return core
