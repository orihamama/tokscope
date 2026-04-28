"""SQLite schema, connection helper, migrations."""
from __future__ import annotations
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .paths import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS file_state (
    path TEXT PRIMARY KEY,
    project TEXT,
    session_id TEXT,
    is_subagent INTEGER DEFAULT 0,
    mtime REAL,
    size INTEGER,
    last_offset INTEGER DEFAULT 0,
    last_uuid TEXT,
    updated_at INTEGER
);

CREATE TABLE IF NOT EXISTS messages (
    uuid TEXT PRIMARY KEY,
    request_id TEXT,
    session_id TEXT,
    project TEXT,
    cwd TEXT,
    git_branch TEXT,
    timestamp INTEGER,
    role TEXT,
    model TEXT,
    service_tier TEXT,
    permission_mode TEXT,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cache_creation INTEGER,
    cache_read INTEGER,
    thinking_tokens INTEGER,
    cost_usd REAL,
    duration_ms INTEGER,
    is_sidechain INTEGER DEFAULT 0,
    parent_tool_use_id TEXT,
    tool_use_id TEXT,
    is_compact_summary INTEGER DEFAULT 0,
    is_api_error INTEGER DEFAULT 0,
    agent_id TEXT,
    source_file TEXT
);

CREATE INDEX IF NOT EXISTS idx_msg_session ON messages(session_id);
CREATE INDEX IF NOT EXISTS idx_msg_ts ON messages(timestamp);
CREATE INDEX IF NOT EXISTS idx_msg_project ON messages(project);
CREATE INDEX IF NOT EXISTS idx_msg_request ON messages(session_id, request_id);

CREATE TABLE IF NOT EXISTS tool_calls (
    id TEXT PRIMARY KEY,
    message_uuid TEXT,
    tool_name TEXT,
    session_id TEXT,
    project TEXT,
    timestamp INTEGER,
    duration_ms INTEGER,
    result_bytes INTEGER,
    result_lines INTEGER,
    result_total_tokens INTEGER,
    is_error INTEGER DEFAULT 0,
    interrupted INTEGER DEFAULT 0,
    user_modified INTEGER DEFAULT 0,
    truncated INTEGER DEFAULT 0,
    exit_code INTEGER,
    attributed_input_tokens INTEGER DEFAULT 0,
    attributed_output_tokens INTEGER DEFAULT 0,
    attributed_cache_creation INTEGER DEFAULT 0,
    attributed_cache_read INTEGER DEFAULT 0,
    attributed_cost_usd REAL DEFAULT 0,
    parent_tool_use_id TEXT,
    bash_command TEXT,
    bash_background INTEGER,
    bash_sandbox_disabled INTEGER,
    file_path TEXT,
    search_pattern TEXT,
    agent_subtype TEXT,
    agent_description TEXT,
    web_url TEXT,
    web_query TEXT,
    agent_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_tool_session ON tool_calls(session_id);
CREATE INDEX IF NOT EXISTS idx_tool_name ON tool_calls(tool_name);
CREATE INDEX IF NOT EXISTS idx_tool_ts ON tool_calls(timestamp);
CREATE INDEX IF NOT EXISTS idx_tool_file ON tool_calls(file_path);
CREATE INDEX IF NOT EXISTS idx_tool_msg ON tool_calls(message_uuid);
CREATE INDEX IF NOT EXISTS idx_tool_agent ON tool_calls(agent_id);
CREATE INDEX IF NOT EXISTS idx_msg_agent ON messages(agent_id);

CREATE TABLE IF NOT EXISTS tasks (
    root_tool_use_id TEXT PRIMARY KEY,
    session_id TEXT,
    project TEXT,
    agent_type TEXT,
    description TEXT,
    started_at INTEGER,
    ended_at INTEGER,
    duration_ms INTEGER,
    total_input INTEGER DEFAULT 0,
    total_output INTEGER DEFAULT 0,
    total_cache_read INTEGER DEFAULT 0,
    total_cache_creation INTEGER DEFAULT 0,
    total_cost_usd REAL DEFAULT 0,
    message_count INTEGER DEFAULT 0,
    tool_call_count INTEGER DEFAULT 0,
    is_error INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_tasks_session ON tasks(session_id);
CREATE INDEX IF NOT EXISTS idx_tasks_cost ON tasks(total_cost_usd);
CREATE INDEX IF NOT EXISTS idx_tasks_subtype ON tasks(agent_type);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    project TEXT,
    cwd TEXT,
    git_branch TEXT,
    started_at INTEGER,
    ended_at INTEGER,
    total_cost_usd REAL DEFAULT 0,
    total_input INTEGER DEFAULT 0,
    total_output INTEGER DEFAULT 0,
    total_cache_read INTEGER DEFAULT 0,
    total_cache_creation INTEGER DEFAULT 0,
    total_thinking_tokens INTEGER DEFAULT 0,
    message_count INTEGER DEFAULT 0,
    tool_call_count INTEGER DEFAULT 0,
    compaction_count INTEGER DEFAULT 0,
    error_count INTEGER DEFAULT 0,
    cache_hit_ratio REAL
);

CREATE INDEX IF NOT EXISTS idx_sessions_project ON sessions(project);
CREATE INDEX IF NOT EXISTS idx_sessions_started ON sessions(started_at);

CREATE TABLE IF NOT EXISTS file_activity (
    session_id TEXT,
    project TEXT,
    file_path TEXT,
    reads INTEGER DEFAULT 0,
    edits INTEGER DEFAULT 0,
    writes INTEGER DEFAULT 0,
    total_lines_added INTEGER DEFAULT 0,
    total_lines_removed INTEGER DEFAULT 0,
    total_cost_usd REAL DEFAULT 0,
    PRIMARY KEY (session_id, file_path)
);

CREATE INDEX IF NOT EXISTS idx_file_activity_path ON file_activity(file_path);
CREATE INDEX IF NOT EXISTS idx_file_activity_project ON file_activity(project);

CREATE TABLE IF NOT EXISTS tool_sequences (
    project TEXT,
    prev_tool TEXT,
    next_tool TEXT,
    count INTEGER DEFAULT 0,
    PRIMARY KEY (project, prev_tool, next_tool)
);
"""

VIEWS = """
DROP VIEW IF EXISTS v_tool_error_rate;
CREATE VIEW v_tool_error_rate AS
SELECT
    tool_name,
    COUNT(*) AS calls,
    SUM(is_error) AS errors,
    CAST(SUM(is_error) AS REAL) / NULLIF(COUNT(*),0) AS error_rate,
    SUM(attributed_cost_usd) AS total_cost
FROM tool_calls
GROUP BY tool_name;

DROP VIEW IF EXISTS v_hourly_heatmap;
CREATE VIEW v_hourly_heatmap AS
SELECT
    CAST(strftime('%w', timestamp/1000, 'unixepoch') AS INTEGER) AS dow,
    CAST(strftime('%H', timestamp/1000, 'unixepoch') AS INTEGER) AS hour,
    SUM(cost_usd) AS cost,
    COUNT(*) AS messages
FROM messages
WHERE timestamp IS NOT NULL
GROUP BY dow, hour;

DROP VIEW IF EXISTS v_daily_spend;
CREATE VIEW v_daily_spend AS
SELECT
    DATE(timestamp/1000, 'unixepoch') AS day,
    project,
    model,
    SUM(input_tokens) AS input_tokens,
    SUM(output_tokens) AS output_tokens,
    SUM(cache_creation) AS cache_creation,
    SUM(cache_read) AS cache_read,
    SUM(thinking_tokens) AS thinking_tokens,
    SUM(cost_usd) AS cost_usd,
    COUNT(*) AS messages
FROM messages
WHERE timestamp IS NOT NULL
GROUP BY day, project, model;
"""


def connect(path: Path | str = DB_PATH) -> sqlite3.Connection:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-64000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn


BASH_COLUMNS = [
    ("bash_program", "TEXT"),
    ("bash_subcommand", "TEXT"),
    ("bash_category", "TEXT"),
    ("bash_pipe_count", "INTEGER"),
    ("bash_has_sudo", "INTEGER"),
]


def _add_columns_if_missing(conn: sqlite3.Connection, table: str, cols: list[tuple[str, str]]) -> None:
    existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    for name, typ in cols:
        if name not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {typ}")


POST_MIGRATE_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_bash_program ON tool_calls(bash_program)",
    "CREATE INDEX IF NOT EXISTS idx_bash_category ON tool_calls(bash_category)",
]


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    _add_columns_if_missing(conn, "tool_calls", BASH_COLUMNS)
    # Discover any plugin-extractor-declared columns and add them idempotently.
    _apply_extractor_schema(conn)
    for stmt in POST_MIGRATE_INDEXES:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass
    # Views: drop+create one at a time so a partial failure can't leave us wedged
    for stmt in [s.strip() for s in VIEWS.split(";") if s.strip()]:
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass


def _apply_extractor_schema(conn: sqlite3.Connection) -> None:
    """Iterate registered extractors; ALTER TABLE ADD COLUMN for any missing
    fields they declare. Tables: 'message' → messages, 'tool_call' → tool_calls.
    """
    try:
        from .plugins import registry
    except Exception:
        return
    registry.load_all()
    target_table = {"message": "messages", "tool_call": "tool_calls"}
    for ex in registry.extractors.values():
        cols = list(ex.fields().items())
        if not cols:
            continue
        for tgt in ex.targets:
            tbl = target_table.get(tgt)
            if tbl:
                _add_columns_if_missing(conn, tbl, cols)
    # Useful indexes for status_class queries (cheap to declare; idempotent).
    for stmt in (
        "CREATE INDEX IF NOT EXISTS idx_tc_status ON tool_calls(status_class)",
        "CREATE INDEX IF NOT EXISTS idx_tc_user_rejection ON tool_calls(is_user_rejection)",
    ):
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError:
            pass


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def get_meta(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def set_meta(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def bump_etag(conn: sqlite3.Connection) -> str:
    cur = int(get_meta(conn, "etag_version", "0") or "0") + 1
    set_meta(conn, "etag_version", str(cur))
    return str(cur)
