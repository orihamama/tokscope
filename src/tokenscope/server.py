"""FastAPI dashboard backend + watcher integration."""
from __future__ import annotations
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import analytics_core
from .db import connect, get_meta, init_schema
from .paths import DB_PATH, PROJECTS_DIR

app = FastAPI(title="Claude Analytics", version="0.1.0")

WEB_DIR = Path(__file__).parent / "web"


_schema_lock = threading.Lock()
_schema_ready = False


def _conn() -> sqlite3.Connection:
    global _schema_ready
    c = connect()
    if not _schema_ready:
        with _schema_lock:
            if not _schema_ready:
                init_schema(c)
                _schema_ready = True
    return c


def _rows(c: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    return [dict(r) for r in c.execute(sql, params).fetchall()]


def _filters(
    project: str | None = None,
    session_id: str | None = None,
    task_id: str | None = None,
    tool: str | None = None,
    since: str | None = None,
    until: str | None = None,
):
    """Return (msg_clause, msg_params, tc_clause, tc_params) WHERE fragments.
    msg_* targets messages table; tc_* targets tool_calls. Both filter on
    the relevant project/session/timestamp; task_id and tool further restrict.
    """
    msg_w: list[str] = []
    msg_p: list = []
    tc_w: list[str] = []
    tc_p: list = []
    if project:
        msg_w.append("project = ?"); msg_p.append(project)
        tc_w.append("project = ?"); tc_p.append(project)
    if session_id:
        msg_w.append("session_id = ?"); msg_p.append(session_id)
        tc_w.append("session_id = ?"); tc_p.append(session_id)
    if task_id:
        # Task = Agent root tool_call. Restrict messages to those carrying the
        # agent_id of this root, plus the root's own message. Restrict tool_calls
        # to either the root itself or those whose containing message has that agent_id.
        msg_w.append(
            "(agent_id = (SELECT agent_id FROM tool_calls WHERE id=?) "
            "OR uuid = (SELECT message_uuid FROM tool_calls WHERE id=?))"
        )
        msg_p.extend([task_id, task_id])
        tc_w.append(
            "(id = ? OR message_uuid IN (SELECT uuid FROM messages "
            "WHERE agent_id = (SELECT agent_id FROM tool_calls WHERE id=?)))"
        )
        tc_p.extend([task_id, task_id])
    if tool:
        tc_w.append("tool_name = ?"); tc_p.append(tool)
    if since:
        try:
            ts = int(since) if since.isdigit() else None
        except Exception:
            ts = None
        if ts is None:
            # treat as ISO date YYYY-MM-DD
            msg_w.append("timestamp >= (strftime('%s', ?)*1000)"); msg_p.append(since)
            tc_w.append("timestamp >= (strftime('%s', ?)*1000)"); tc_p.append(since)
        else:
            msg_w.append("timestamp >= ?"); msg_p.append(ts)
            tc_w.append("timestamp >= ?"); tc_p.append(ts)
    if until:
        try:
            ts = int(until) if until.isdigit() else None
        except Exception:
            ts = None
        if ts is None:
            msg_w.append("timestamp <= (strftime('%s', ?)*1000)"); msg_p.append(until)
            tc_w.append("timestamp <= (strftime('%s', ?)*1000)"); tc_p.append(until)
        else:
            msg_w.append("timestamp <= ?"); msg_p.append(ts)
            tc_w.append("timestamp <= ?"); tc_p.append(ts)
    msg_clause = (" WHERE " + " AND ".join(msg_w)) if msg_w else ""
    tc_clause = (" WHERE " + " AND ".join(tc_w)) if tc_w else ""
    return msg_clause, tuple(msg_p), tc_clause, tuple(tc_p)


def _etag() -> str:
    c = _conn()
    return get_meta(c, "etag_version", "0") or "0"


def _maybe_304(req: Request, etag: str) -> Response | None:
    inm = req.headers.get("if-none-match")
    if inm == etag:
        return Response(status_code=304, headers={"ETag": etag})
    return None


def _json(payload: Any, etag: str) -> JSONResponse:
    return JSONResponse(payload, headers={"ETag": etag, "Cache-Control": "no-cache"})


# ----------------------------------------------------------------------
@app.get("/api/filter-options")
def filter_options(req: Request):
    """Lookup lists for filter dropdowns: projects, sessions, tools, tasks."""
    etag = _etag()
    if r := _maybe_304(req, etag):
        return r
    c = _conn()
    projects = _rows(c, """
        SELECT project, ROUND(SUM(total_cost_usd),4) cost, COUNT(*) sessions
        FROM sessions GROUP BY project ORDER BY cost DESC""")
    sessions = _rows(c, """
        SELECT session_id id, project, started_at,
               ROUND(total_cost_usd,4) cost, message_count msgs
        FROM sessions ORDER BY total_cost_usd DESC LIMIT 500""")
    tools = _rows(c, """
        SELECT tool_name, COUNT(*) calls, ROUND(SUM(attributed_cost_usd),4) cost
        FROM tool_calls GROUP BY tool_name ORDER BY cost DESC""")
    tasks = _rows(c, """
        SELECT root_tool_use_id id, agent_type, description, project,
               ROUND(total_cost_usd,4) cost, message_count msgs
        FROM tasks ORDER BY total_cost_usd DESC LIMIT 500""")
    return _json({"projects": projects, "sessions": sessions, "tools": tools, "tasks": tasks}, etag)


# ----------------------------------------------------------------------
@app.get("/api/overview")
def overview(
    req: Request,
    project: str | None = None,
    session_id: str | None = None,
    task_id: str | None = None,
    tool: str | None = None,
    since: str | None = None,
    until: str | None = None,
):
    etag = _etag() + "|" + ",".join(filter(None, [project, session_id, task_id, tool, since, until]))
    if r := _maybe_304(req, etag):
        return r
    payload = analytics_core.overview({
        "project": project, "session_id": session_id, "task_id": task_id,
        "tool": tool, "since": since, "until": until,
    })
    return _json(payload, etag)


@app.get("/api/insights")
def insights(
    req: Request,
    project: str | None = None,
    session_id: str | None = None,
    task_id: str | None = None,
    tool: str | None = None,
    since: str | None = None,
    until: str | None = None,
):
    """Composite bottleneck report: duplicate reads, bash retries, error chains,
    cost outliers, compaction root causes, reasoning+cache aggregations."""
    etag = _etag() + "|insights|" + ",".join(filter(None,
        [project, session_id, task_id, tool, since, until]))
    if r := _maybe_304(req, etag):
        return r
    payload = analytics_core.insights({
        "project": project, "session_id": session_id, "task_id": task_id,
        "tool": tool, "since": since, "until": until,
    })
    return _json(payload, etag)


@app.get("/api/tools")
def tools(
    req: Request,
    project: str | None = None,
    session_id: str | None = None,
    task_id: str | None = None,
    tool: str | None = None,
    since: str | None = None,
    until: str | None = None,
):
    etag = _etag() + "|t|" + ",".join(filter(None, [project, session_id, task_id, tool, since, until]))
    if r := _maybe_304(req, etag):
        return r
    c = _conn()
    _, _, tc_w, tc_p = _filters(project, session_id, task_id, tool, since, until)
    rows = _rows(c, f"""
        SELECT
            tool_name,
            COUNT(*) AS calls,
            SUM(is_error) AS errors,
            CAST(SUM(is_error) AS REAL) / NULLIF(COUNT(*),0) AS error_rate,
            SUM(interrupted) AS interrupted,
            SUM(user_modified) AS user_modified,
            SUM(truncated) AS truncated,
            ROUND(AVG(NULLIF(duration_ms,0)),0) AS avg_ms,
            SUM(attributed_input_tokens) AS in_tokens,
            SUM(attributed_output_tokens) AS out_tokens,
            ROUND(SUM(attributed_cost_usd),4) AS cost,
            SUM(result_bytes) AS total_bytes
        FROM tool_calls{tc_w}
        GROUP BY tool_name
        ORDER BY cost DESC""", tc_p)
    return _json({"tools": rows}, etag)


@app.get("/api/tools/{name}")
def tool_detail(name: str, req: Request):
    etag = _etag()
    if r := _maybe_304(req, etag):
        return r
    c = _conn()
    summary = c.execute(
        """SELECT COUNT(*) calls, SUM(is_error) errors,
                  SUM(interrupted) interrupted, SUM(user_modified) user_modified,
                  SUM(truncated) truncated,
                  ROUND(AVG(NULLIF(duration_ms,0)),0) avg_ms,
                  SUM(attributed_input_tokens) in_tokens,
                  SUM(attributed_output_tokens) out_tokens,
                  ROUND(SUM(attributed_cost_usd),4) cost
           FROM tool_calls WHERE tool_name=?""", (name,)).fetchone()
    if not summary:
        raise HTTPException(404)
    by_session = _rows(c, """
        SELECT session_id, project, COUNT(*) calls,
               ROUND(SUM(attributed_cost_usd),4) cost
        FROM tool_calls WHERE tool_name=?
        GROUP BY session_id, project ORDER BY cost DESC LIMIT 20""", (name,))
    samples_sql = "SELECT id, timestamp, attributed_cost_usd cost"
    if name == "Bash":
        samples_sql += ", bash_command AS detail"
    elif name in ("Read", "Edit", "Write"):
        samples_sql += ", file_path AS detail"
    elif name in ("Grep", "Glob"):
        samples_sql += ", search_pattern AS detail"
    elif name == "WebFetch":
        samples_sql += ", web_url AS detail"
    elif name == "WebSearch":
        samples_sql += ", web_query AS detail"
    elif name == "Agent":
        samples_sql += ", agent_description AS detail"
    else:
        samples_sql += ", NULL AS detail"
    samples_sql += " FROM tool_calls WHERE tool_name=? ORDER BY attributed_cost_usd DESC LIMIT 30"
    samples = _rows(c, samples_sql, (name,))
    return _json({"summary": dict(summary), "by_session": by_session, "samples": samples}, etag)


@app.get("/api/tasks")
def tasks(
    req: Request,
    project: str | None = None,
    session_id: str | None = None,
    task_id: str | None = None,
    tool: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 200,
):
    etag = _etag() + "|tk|" + ",".join(filter(None, [project, session_id, task_id, tool, since, until]))
    if r := _maybe_304(req, etag):
        return r
    c = _conn()
    where_parts: list[str] = []
    params: list = []
    if project:
        where_parts.append("project = ?"); params.append(project)
    if session_id:
        where_parts.append("session_id = ?"); params.append(session_id)
    if task_id:
        where_parts.append("root_tool_use_id = ?"); params.append(task_id)
    if since:
        where_parts.append("started_at >= (strftime('%s', ?)*1000)"); params.append(since)
    if until:
        where_parts.append("started_at <= (strftime('%s', ?)*1000)"); params.append(until)
    where = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""
    by_type = _rows(c, f"""
        SELECT agent_type, COUNT(*) tasks,
               ROUND(AVG(duration_ms)/1000.0,1) avg_s,
               SUM(message_count) msgs,
               ROUND(SUM(total_cost_usd),4) cost
        FROM tasks{where} GROUP BY agent_type ORDER BY cost DESC""", tuple(params))
    items = _rows(c, f"""
        SELECT root_tool_use_id id, agent_type, description,
               session_id, project,
               started_at, ended_at, duration_ms,
               message_count, tool_call_count,
               total_input, total_output,
               ROUND(total_cost_usd,4) cost,
               is_error
        FROM tasks{where} ORDER BY total_cost_usd DESC LIMIT ?""",
        tuple(params) + (limit,))
    return _json({"by_type": by_type, "items": items}, etag)


@app.get("/api/projects")
def projects(
    req: Request,
    project: str | None = None,
    session_id: str | None = None,
    task_id: str | None = None,
    tool: str | None = None,
    since: str | None = None,
    until: str | None = None,
):
    etag = _etag() + "|p|" + ",".join(filter(None, [project, session_id, task_id, tool, since, until]))
    if r := _maybe_304(req, etag):
        return r
    c = _conn()
    msg_w, msg_p, tc_w, tc_p = _filters(project, session_id, task_id, tool, since, until)
    items = _rows(c, f"""
        SELECT project, COUNT(DISTINCT session_id) sessions,
               COUNT(*) msgs,
               ROUND(SUM(cost_usd),4) cost,
               SUM(is_compact_summary) compactions,
               SUM(is_api_error) errors
        FROM messages{msg_w} GROUP BY project ORDER BY cost DESC""", msg_p)
    timeline = _rows(c, f"""
        SELECT DATE(timestamp/1000,'unixepoch') day, project,
               ROUND(SUM(cost_usd),4) cost
        FROM messages{msg_w} {'AND' if msg_w else 'WHERE'} timestamp IS NOT NULL
        GROUP BY day, project ORDER BY day""", msg_p)
    tool_mix = _rows(c, f"""
        SELECT project, tool_name, COUNT(*) calls
        FROM tool_calls{tc_w} GROUP BY project, tool_name""", tc_p)
    return _json({"projects": items, "timeline": timeline, "tool_mix": tool_mix}, etag)


@app.get("/api/sessions")
def sessions_list(
    req: Request,
    project: str | None = None,
    session_id: str | None = None,
    task_id: str | None = None,
    tool: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 200,
):
    etag = _etag() + "|s|" + ",".join(filter(None, [project, session_id, task_id, tool, since, until]))
    if r := _maybe_304(req, etag):
        return r
    c = _conn()
    where_parts: list[str] = []
    params: list = []
    if project:
        where_parts.append("project = ?"); params.append(project)
    if session_id:
        where_parts.append("session_id = ?"); params.append(session_id)
    if since:
        where_parts.append("started_at >= (strftime('%s', ?)*1000)"); params.append(since)
    if until:
        where_parts.append("started_at <= (strftime('%s', ?)*1000)"); params.append(until)
    if task_id:
        where_parts.append(
            "session_id = (SELECT session_id FROM tasks WHERE root_tool_use_id=?)"
        )
        params.append(task_id)
    if tool:
        where_parts.append(
            "session_id IN (SELECT DISTINCT session_id FROM tool_calls WHERE tool_name=?)"
        )
        params.append(tool)
    where = (" WHERE " + " AND ".join(where_parts)) if where_parts else ""
    items = _rows(c, f"""
        SELECT session_id, project, started_at, ended_at,
               message_count, tool_call_count, compaction_count, error_count,
               ROUND(total_cost_usd,4) cost,
               ROUND(cache_hit_ratio,3) cache_hit
        FROM sessions{where} ORDER BY total_cost_usd DESC LIMIT ?""",
        tuple(params) + (limit,))
    return _json({"sessions": items}, etag)


@app.get("/api/sessions/{session_id}")
def session_detail(session_id: str, req: Request):
    etag = _etag()
    if r := _maybe_304(req, etag):
        return r
    c = _conn()
    sess = c.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
    if not sess:
        raise HTTPException(404)
    timeline = _rows(c, """
        SELECT timestamp, role, model, input_tokens, output_tokens,
               cache_creation, cache_read, cost_usd,
               is_compact_summary, is_api_error
        FROM messages WHERE session_id=? AND timestamp IS NOT NULL
        ORDER BY timestamp""", (session_id,))
    tool_breakdown = _rows(c, """
        SELECT tool_name, COUNT(*) calls,
               ROUND(SUM(attributed_cost_usd),4) cost,
               SUM(is_error) errors
        FROM tool_calls WHERE session_id=?
        GROUP BY tool_name ORDER BY cost DESC""", (session_id,))
    return _json({"session": dict(sess), "timeline": timeline, "tools": tool_breakdown}, etag)


@app.get("/api/files")
def files(
    req: Request,
    project: str | None = None,
    session_id: str | None = None,
    task_id: str | None = None,
    tool: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 100,
):
    etag = _etag() + "|f|" + ",".join(filter(None, [project, session_id, task_id, tool, since, until]))
    if r := _maybe_304(req, etag):
        return r
    c = _conn()
    # Use tool_calls for accurate filtering (file_activity is materialized at session granularity)
    _, _, tc_w, tc_p = _filters(project, session_id, task_id, tool, since, until)
    file_filter = " AND file_path IS NOT NULL AND tool_name IN ('Read','Edit','Write','MultiEdit')"
    if tc_w:
        sql = f"""SELECT file_path,
                   SUM(CASE WHEN tool_name='Read' THEN 1 ELSE 0 END) reads,
                   SUM(CASE WHEN tool_name='Edit' THEN 1 ELSE 0 END) edits,
                   SUM(CASE WHEN tool_name='Write' THEN 1 ELSE 0 END) writes,
                   COUNT(*) total,
                   ROUND(SUM(attributed_cost_usd),4) cost,
                   COUNT(DISTINCT session_id) sessions
                FROM tool_calls{tc_w}{file_filter}
                GROUP BY file_path ORDER BY total DESC LIMIT ?"""
        hotspots = _rows(c, sql, tc_p + (limit,))
        sql2 = f"""SELECT file_path, session_id, project, COUNT(*) reads
                FROM tool_calls{tc_w} AND tool_name='Read' AND file_path IS NOT NULL
                GROUP BY file_path, session_id HAVING reads >= 5
                ORDER BY reads DESC LIMIT 50"""
        rereads = _rows(c, sql2, tc_p)
    else:
        hotspots = _rows(c, """
            SELECT file_path,
                   SUM(reads) reads, SUM(edits) edits, SUM(writes) writes,
                   (SUM(reads)+SUM(edits)+SUM(writes)) total,
                   ROUND(SUM(total_cost_usd),4) cost,
                   COUNT(DISTINCT session_id) sessions
            FROM file_activity GROUP BY file_path
            ORDER BY total DESC LIMIT ?""", (limit,))
        rereads = _rows(c, """
            SELECT file_path, session_id, project, reads
            FROM file_activity WHERE reads >= 5
            ORDER BY reads DESC LIMIT 50""")
    return _json({"hotspots": hotspots, "rereads": rereads}, etag)


@app.get("/api/bash")
def bash_stats(
    req: Request,
    project: str | None = None,
    session_id: str | None = None,
    task_id: str | None = None,
    since: str | None = None,
    until: str | None = None,
):
    etag = _etag() + "|b|" + ",".join(filter(None, [project, session_id, task_id, since, until]))
    if r := _maybe_304(req, etag):
        return r
    c = _conn()
    _, _, tc_w, tc_p = _filters(project, session_id, task_id, "Bash", since, until)
    summary = c.execute(f"""
        SELECT COUNT(*) n,
               SUM(is_error) errors,
               SUM(bash_background) backgrounds,
               SUM(bash_sandbox_disabled) sandbox_off,
               ROUND(AVG(NULLIF(duration_ms,0)),0) avg_ms,
               ROUND(SUM(attributed_cost_usd),4) cost
        FROM tool_calls{tc_w}""", tc_p).fetchone()
    top_commands = _rows(c, f"""
        SELECT SUBSTR(bash_command,1,80) command, COUNT(*) n,
               SUM(is_error) errs,
               ROUND(SUM(attributed_cost_usd),4) cost
        FROM tool_calls{tc_w} AND bash_command IS NOT NULL
        GROUP BY SUBSTR(bash_command,1,80)
        ORDER BY n DESC LIMIT 30""", tc_p)
    exit_codes = _rows(c, f"""
        SELECT exit_code, COUNT(*) n FROM tool_calls{tc_w} AND exit_code IS NOT NULL
        GROUP BY exit_code ORDER BY n DESC""", tc_p)
    return _json({"summary": dict(summary) if summary else {}, "top_commands": top_commands, "exit_codes": exit_codes}, etag)


@app.get("/api/search")
def search_stats(
    req: Request,
    project: str | None = None,
    session_id: str | None = None,
    task_id: str | None = None,
    since: str | None = None,
    until: str | None = None,
):
    etag = _etag() + "|sr|" + ",".join(filter(None, [project, session_id, task_id, since, until]))
    if r := _maybe_304(req, etag):
        return r
    c = _conn()
    def w(tool_name: str):
        return _filters(project, session_id, task_id, tool_name, since, until)[2:]
    g_w, g_p = w("Grep")
    grep = _rows(c, f"""
        SELECT search_pattern pattern, COUNT(*) n,
               ROUND(SUM(attributed_cost_usd),4) cost
        FROM tool_calls{g_w} AND search_pattern IS NOT NULL
        GROUP BY search_pattern ORDER BY n DESC LIMIT 30""", g_p)
    gl_w, gl_p = w("Glob")
    glob = _rows(c, f"""
        SELECT search_pattern pattern, COUNT(*) n
        FROM tool_calls{gl_w} AND search_pattern IS NOT NULL
        GROUP BY search_pattern ORDER BY n DESC LIMIT 30""", gl_p)
    wf_w, wf_p = w("WebFetch")
    web_fetch = _rows(c, f"""
        SELECT web_url url, COUNT(*) n,
               ROUND(SUM(attributed_cost_usd),4) cost
        FROM tool_calls{wf_w} AND web_url IS NOT NULL
        GROUP BY web_url ORDER BY n DESC LIMIT 30""", wf_p)
    ws_w, ws_p = w("WebSearch")
    web_search = _rows(c, f"""
        SELECT web_query query, COUNT(*) n
        FROM tool_calls{ws_w} AND web_query IS NOT NULL
        GROUP BY web_query ORDER BY n DESC LIMIT 30""", ws_p)
    return _json({"grep": grep, "glob": glob, "web_fetch": web_fetch, "web_search": web_search}, etag)


@app.get("/api/workflow")
def workflow(
    req: Request,
    project: str | None = None,
    session_id: str | None = None,
    task_id: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 60,
):
    etag = _etag() + "|w|" + ",".join(filter(None, [project, session_id, task_id, since, until]))
    if r := _maybe_304(req, etag):
        return r
    c = _conn()
    if project or session_id or task_id or since or until:
        # Recompute bigrams on-the-fly under filter
        _, _, tc_w, tc_p = _filters(project, session_id, task_id, None, since, until)
        bigrams = _rows(c, f"""
            SELECT prev_tool, next_tool, COUNT(*) n FROM (
                SELECT tool_name AS next_tool,
                       LAG(tool_name) OVER (PARTITION BY session_id ORDER BY timestamp, id) AS prev_tool
                FROM tool_calls{tc_w} {'AND' if tc_w else 'WHERE'} timestamp IS NOT NULL
            ) WHERE prev_tool IS NOT NULL
            GROUP BY prev_tool, next_tool ORDER BY n DESC LIMIT ?""", tc_p + (limit,))
    else:
        bigrams = _rows(c, """
            SELECT prev_tool, next_tool, SUM(count) n
            FROM tool_sequences GROUP BY prev_tool, next_tool
            ORDER BY n DESC LIMIT ?""", (limit,))
    msg_w, msg_p, _, _ = _filters(project, session_id, task_id, None, since, until)
    if msg_w:
        permission_modes = _rows(c, f"""
            SELECT COALESCE(permission_mode,'unset') mode, COUNT(*) n
            FROM messages{msg_w} AND role='assistant'
            GROUP BY permission_mode ORDER BY n DESC""", msg_p)
    else:
        permission_modes = _rows(c, """
            SELECT COALESCE(permission_mode,'unset') mode, COUNT(*) n
            FROM messages WHERE role='assistant'
            GROUP BY permission_mode ORDER BY n DESC""")
    return _json({"bigrams": bigrams, "permission_modes": permission_modes}, etag)


@app.get("/api/health")
def health(
    req: Request,
    project: str | None = None,
    session_id: str | None = None,
    task_id: str | None = None,
    since: str | None = None,
    until: str | None = None,
):
    etag = _etag() + "|h|" + ",".join(filter(None, [project, session_id, task_id, since, until]))
    if r := _maybe_304(req, etag):
        return r
    c = _conn()
    msg_w, msg_p, tc_w, tc_p = _filters(project, session_id, task_id, None, since, until)
    api_errors_timeline = _rows(c, f"""
        SELECT DATE(timestamp/1000,'unixepoch') day, SUM(is_api_error) errors
        FROM messages{msg_w} {'AND' if msg_w else 'WHERE'} timestamp IS NOT NULL
        GROUP BY day ORDER BY day DESC LIMIT 60""", msg_p)
    sess_w_parts: list[str] = []
    sess_params: list = []
    if project:
        sess_w_parts.append("project = ?"); sess_params.append(project)
    if session_id:
        sess_w_parts.append("session_id = ?"); sess_params.append(session_id)
    sess_w = (" AND " + " AND ".join(sess_w_parts)) if sess_w_parts else ""
    compactions = _rows(c, f"""
        SELECT session_id, project, compaction_count, ROUND(total_cost_usd,4) cost
        FROM sessions WHERE compaction_count>0{sess_w}
        ORDER BY compaction_count DESC LIMIT 30""", tuple(sess_params))
    error_tools = _rows(c, f"""
        SELECT tool_name, COUNT(*) errors
        FROM tool_calls{tc_w} {'AND' if tc_w else 'WHERE'} is_error=1
        GROUP BY tool_name ORDER BY errors DESC""", tc_p)
    long_sessions = _rows(c, f"""
        SELECT session_id, project, message_count, ROUND(total_cost_usd,4) cost
        FROM sessions WHERE message_count > 500{sess_w}
        ORDER BY message_count DESC LIMIT 20""", tuple(sess_params))
    return _json({
        "api_errors_timeline": api_errors_timeline,
        "compactions": compactions,
        "error_tools": error_tools,
        "long_sessions": long_sessions,
    }, etag)


@app.get("/api/ledger")
def ledger(
    req: Request,
    range: str = "30d",
    project: str | None = None,
    session_id: str | None = None,
    task_id: str | None = None,
    tool: str | None = None,
):
    etag = _etag() + "|l|" + ",".join(filter(None, [range, project, session_id, task_id, tool]))
    if r := _maybe_304(req, etag):
        return r
    c = _conn()
    since = None
    if range != "all" and range.endswith("d") and range[:-1].isdigit():
        n = int(range[:-1])
        since = f"-{n} days"
    msg_w, msg_p, _, _ = _filters(project, session_id, task_id, None, None, None)
    range_clause = ""
    range_params: tuple = ()
    if since:
        range_clause = f"{'AND' if msg_w else 'WHERE'} timestamp >= (strftime('%s','now',?)*1000)"
        range_params = (since,)
    daily = _rows(c, f"""
        SELECT DATE(timestamp/1000,'unixepoch') day,
               ROUND(SUM(cost_usd),4) cost,
               SUM(input_tokens) input, SUM(output_tokens) output,
               SUM(cache_read) cache_read, SUM(cache_creation) cache_creation,
               SUM(thinking_tokens) thinking
        FROM messages{msg_w} {range_clause}
          {'AND' if (msg_w or range_clause) else 'WHERE'} timestamp IS NOT NULL
        GROUP BY day ORDER BY day""", msg_p + range_params)
    by_model = _rows(c, f"""
        SELECT model, ROUND(SUM(cost_usd),4) cost,
               SUM(input_tokens) input, SUM(output_tokens) output
        FROM messages{msg_w} {range_clause}
        GROUP BY model ORDER BY cost DESC""", msg_p + range_params)
    return _json({"daily": daily, "by_model": by_model}, etag)


@app.get("/api/heatmap")
def heatmap(
    req: Request,
    project: str | None = None,
    session_id: str | None = None,
    task_id: str | None = None,
    tool: str | None = None,
    since: str | None = None,
    until: str | None = None,
):
    etag = _etag() + "|hm|" + ",".join(filter(None, [project, session_id, task_id, tool, since, until]))
    if r := _maybe_304(req, etag):
        return r
    c = _conn()
    if tool:
        _, _, tc_w, tc_p = _filters(project, session_id, task_id, tool, since, until)
        rows = _rows(c, f"""
            SELECT
                CAST(strftime('%w', timestamp/1000, 'unixepoch') AS INTEGER) AS dow,
                CAST(strftime('%H', timestamp/1000, 'unixepoch') AS INTEGER) AS hour,
                ROUND(SUM(attributed_cost_usd),4) AS cost,
                COUNT(*) AS messages
            FROM tool_calls{tc_w} {'AND' if tc_w else 'WHERE'} timestamp IS NOT NULL
            GROUP BY dow, hour""", tc_p)
    else:
        msg_w, msg_p, _, _ = _filters(project, session_id, task_id, None, since, until)
        rows = _rows(c, f"""
            SELECT
                CAST(strftime('%w', timestamp/1000, 'unixepoch') AS INTEGER) AS dow,
                CAST(strftime('%H', timestamp/1000, 'unixepoch') AS INTEGER) AS hour,
                ROUND(SUM(cost_usd),4) AS cost,
                COUNT(*) AS messages
            FROM messages{msg_w} {'AND' if msg_w else 'WHERE'} timestamp IS NOT NULL
            GROUP BY dow, hour""", msg_p)
    return _json({"cells": rows}, etag)


# ----------------------------------------------------------------------
# Treemap (full hierarchy in one payload, capped per level)
@app.get("/api/treemap")
def treemap(
    req: Request,
    project: str | None = None,
    session_id: str | None = None,
    task_id: str | None = None,
    tool: str | None = None,
    since: str | None = None,
    until: str | None = None,
    max_per_level: int = 50,
):
    etag = _etag() + "|tm|" + ",".join(filter(None, [project, session_id, task_id, tool, since, until, str(max_per_level)]))
    if r := _maybe_304(req, etag):
        return r
    c = _conn()
    msg_w, msg_p, tc_w, tc_p = _filters(project, session_id, task_id, tool, since, until)

    # 1) Project totals
    proj_rows = _rows(c, f"""
        SELECT project, ROUND(SUM(cost_usd),4) cost
        FROM messages{msg_w}
        GROUP BY project HAVING cost > 0
        ORDER BY cost DESC LIMIT ?""", msg_p + (max_per_level,))

    # 2) For each project, take top sessions
    project_to_sessions: dict[str, list[dict]] = {}
    for p in proj_rows:
        sw = list(msg_w[len(" WHERE "):].split(" AND ")) if msg_w else []
        # Re-add project filter explicitly for sub-query
        sub_where = "project = ?"
        sub_params: list = [p["project"]]
        if since:
            sub_where += " AND timestamp >= (strftime('%s', ?)*1000)"; sub_params.append(since)
        if until:
            sub_where += " AND timestamp <= (strftime('%s', ?)*1000)"; sub_params.append(until)
        if session_id:
            sub_where += " AND session_id = ?"; sub_params.append(session_id)
        sess = _rows(c, f"""
            SELECT session_id, ROUND(SUM(cost_usd),4) cost
            FROM messages WHERE {sub_where}
            GROUP BY session_id HAVING cost > 0
            ORDER BY cost DESC LIMIT ?""", tuple(sub_params) + (max_per_level,))
        project_to_sessions[p["project"]] = sess

    # 3) For each session, fetch tasks (rooted in that session) + direct tools
    session_to_children: dict[str, dict] = {}
    all_session_ids = [s["session_id"] for ss in project_to_sessions.values() for s in ss]
    if all_session_ids:
        placeholders = ",".join(["?"] * len(all_session_ids))
        tasks_rows = _rows(c, f"""
            SELECT root_tool_use_id id, session_id, agent_type, description,
                   ROUND(total_cost_usd,4) cost
            FROM tasks WHERE session_id IN ({placeholders}) AND total_cost_usd > 0
            ORDER BY total_cost_usd DESC""", tuple(all_session_ids))
        # Direct tools per session: tools NOT inside any sub-agent
        tools_rows = _rows(c, f"""
            SELECT tc.session_id, tc.tool_name,
                   COUNT(*) calls,
                   ROUND(SUM(tc.attributed_cost_usd),4) cost
            FROM tool_calls tc
            JOIN messages m ON m.uuid = tc.message_uuid
            WHERE tc.session_id IN ({placeholders})
              AND COALESCE(m.is_sidechain,0) = 0
              AND tc.attributed_cost_usd > 0
            GROUP BY tc.session_id, tc.tool_name""", tuple(all_session_ids))
        for sid in all_session_ids:
            session_to_children[sid] = {"tasks": [], "tools": []}
        for t in tasks_rows:
            session_to_children[t["session_id"]]["tasks"].append(t)
        for t in tools_rows:
            session_to_children[t["session_id"]]["tools"].append(t)

    # 4) Tools used INSIDE each task (aggregated by tool_name)
    task_ids = [t["id"] for ch in session_to_children.values() for t in ch["tasks"]]
    task_to_tools: dict[str, list[dict]] = {}
    if task_ids:
        # Use single query: link tools whose containing message has agent_id matching
        # the task's agent_id. Cheaper than per-task subqueries.
        agent_map_rows = c.execute(
            f"SELECT id, agent_id FROM tool_calls WHERE id IN "
            f"({','.join('?'*len(task_ids))})",
            task_ids,
        ).fetchall()
        agent_to_task = {r["agent_id"]: r["id"] for r in agent_map_rows if r["agent_id"]}
        if agent_to_task:
            agent_ids = list(agent_to_task.keys())
            tool_rows = _rows(c, f"""
                SELECT m.agent_id, tc.tool_name,
                       COUNT(*) calls,
                       ROUND(SUM(tc.attributed_cost_usd),4) cost
                FROM tool_calls tc
                JOIN messages m ON m.uuid = tc.message_uuid
                WHERE m.agent_id IN ({','.join('?'*len(agent_ids))})
                  AND tc.attributed_cost_usd > 0
                GROUP BY m.agent_id, tc.tool_name""", tuple(agent_ids))
            for r in tool_rows:
                tid = agent_to_task.get(r["agent_id"])
                if tid:
                    task_to_tools.setdefault(tid, []).append(r)

    # Assemble nested structure
    def child_node(name, value, kind, children=None, **extra):
        d = {"name": name, "value": value, "kind": kind}
        if children:
            d["children"] = children
        d.update(extra)
        return d

    def add_residual(parent_value: float, kids: list[dict], label: str = "(unattributed)") -> list[dict]:
        """If sum(children) < parent value, append a residual leaf so math reconciles.
        Residual = cache_read tokens + reasoning/text turns not tied to any specific tool."""
        kids_sum = sum(k["value"] for k in kids)
        gap = round(parent_value - kids_sum, 4)
        if gap > 0.001:
            kids.append({
                "name": label,
                "value": gap,
                "kind": "other",
            })
        return kids

    project_nodes = []
    for p in proj_rows:
        sessions = []
        for s in project_to_sessions.get(p["project"], []):
            ch = session_to_children.get(s["session_id"], {"tasks": [], "tools": []})
            session_kids = []
            for t in ch["tasks"]:
                tools_under = task_to_tools.get(t["id"], [])
                tool_kids = [child_node(tl["tool_name"], tl["cost"], "tool", calls=tl["calls"])
                             for tl in tools_under]
                tool_kids = add_residual(t["cost"], tool_kids, "(reasoning + cache)")
                session_kids.append(child_node(
                    f"[{t['agent_type']}] {(t['description'] or '')[:50]}",
                    t["cost"], "task",
                    id=t["id"],
                    children=tool_kids,
                ))
            for tl in ch["tools"]:
                session_kids.append(child_node(tl["tool_name"], tl["cost"], "tool", calls=tl["calls"]))
            session_kids = add_residual(s["cost"], session_kids, "(reasoning + cache)")
            sessions.append(child_node(s["session_id"][:8], s["cost"], "session",
                                       children=session_kids, full_id=s["session_id"]))
        project_nodes.append(child_node(p["project"], p["cost"], "project", children=sessions))

    total = sum(p["value"] for p in project_nodes)
    return _json({
        "root": {"name": "All", "value": round(total, 4), "kind": "root", "children": project_nodes}
    }, etag)


# ----------------------------------------------------------------------
# Lazy residual ("reasoning + cache") drill-down
@app.get("/api/treemap/residual")
def treemap_residual(
    req: Request,
    scope_kind: str,                  # "session" or "task"
    scope_id: str,
    project: str | None = None,
):
    if scope_kind not in ("session", "task"):
        raise HTTPException(400, "scope_kind must be 'session' or 'task'")
    etag = _etag() + "|tmr|" + scope_kind + ":" + scope_id + (project or "")
    if r := _maybe_304(req, etag):
        return r
    c = _conn()

    # Build messages WHERE clause for the scope
    where: list[str] = []
    params: list = []
    if scope_kind == "session":
        where.append("session_id = ?"); params.append(scope_id)
    else:
        where.append(
            "agent_id = (SELECT agent_id FROM tool_calls WHERE id = ?) "
            "OR uuid = (SELECT message_uuid FROM tool_calls WHERE id = ?)"
        )
        params.extend([scope_id, scope_id])
    if project:
        where.append("project = ?"); params.append(project)
    msg_w = " WHERE " + " AND ".join(where) if where else ""

    # Fetch messages with usage + model so we can apportion cost by token type
    msgs = _rows(c, f"""
        SELECT uuid, timestamp, model, role,
               input_tokens, output_tokens,
               cache_creation, cache_read,
               thinking_tokens, cost_usd
        FROM messages{msg_w}
        ORDER BY timestamp""", tuple(params))

    # Total attributed cost (already billed to tool_calls)
    if scope_kind == "session":
        attrib_sql = """SELECT COALESCE(SUM(attributed_cost_usd),0)
                        FROM tool_calls WHERE session_id = ?"""
        attrib = c.execute(attrib_sql, (scope_id,)).fetchone()[0]
    else:
        attrib_sql = """SELECT COALESCE(SUM(attributed_cost_usd),0) FROM tool_calls
                        WHERE id = ? OR message_uuid IN (
                          SELECT uuid FROM messages WHERE agent_id = (
                            SELECT agent_id FROM tool_calls WHERE id = ?))"""
        attrib = c.execute(attrib_sql, (scope_id, scope_id)).fetchone()[0]

    # Component cost rollups using LiteLLM/fallback prices
    cache_read_cost = 0.0
    cache_creation_cost = 0.0
    thinking_cost = 0.0
    output_total = 0.0
    input_total = 0.0
    text_only_msgs: list[dict] = []
    cache_read_turns: list[dict] = []
    cache_creation_turns: list[dict] = []
    thinking_turns: list[dict] = []
    total_msg_cost = 0.0

    # Build set of message uuids that have tool_use blocks (= "had tools")
    has_tool_msg_ids = {
        r["uuid"] for r in c.execute(
            f"""SELECT DISTINCT uuid FROM messages{msg_w}
                  AND uuid IN (SELECT DISTINCT message_uuid FROM tool_calls)
            """, tuple(params)
        ).fetchall()
    } if msg_w else {
        r["uuid"] for r in c.execute(
            "SELECT DISTINCT message_uuid uuid FROM tool_calls"
        ).fetchall()
    }

    from .pricing import price_for
    for m in msgs:
        p = price_for(m["model"])
        cr = (m["cache_read"] or 0) * p["cache_read"]
        cc = (m["cache_creation"] or 0) * p["cache_creation"]
        oc = (m["output_tokens"] or 0) * p["output"]
        ic = (m["input_tokens"] or 0) * p["input"]
        tc = (m["thinking_tokens"] or 0) * p["output"]
        cache_read_cost += cr
        cache_creation_cost += cc
        thinking_cost += tc
        output_total += oc
        input_total += ic
        total_msg_cost += (m["cost_usd"] or 0)
        # Per-turn detail records
        if cr > 0.0001:
            cache_read_turns.append({
                "uuid": m["uuid"], "timestamp": m["timestamp"], "model": m["model"],
                "cache_read_tokens": m["cache_read"] or 0,
                "input_tokens": m["input_tokens"] or 0,
                "cost": round(cr, 4),
                "hit_ratio": round((m["cache_read"] or 0) / max(1, (m["cache_read"] or 0) + (m["input_tokens"] or 0)), 4),
            })
        if cc > 0.0001:
            cache_creation_turns.append({
                "uuid": m["uuid"], "timestamp": m["timestamp"], "model": m["model"],
                "cache_creation_tokens": m["cache_creation"] or 0,
                "cost": round(cc, 4),
            })
        if tc > 0.0001 and (m["thinking_tokens"] or 0) > 0:
            thinking_turns.append({
                "uuid": m["uuid"], "timestamp": m["timestamp"], "model": m["model"],
                "thinking_tokens": m["thinking_tokens"] or 0,
                "output_tokens": m["output_tokens"] or 0,
                "ratio": round((m["thinking_tokens"] or 0) / max(1, m["output_tokens"] or 0), 3),
                "cost": round(tc, 4),
            })
        if m["role"] == "assistant" and m["uuid"] not in has_tool_msg_ids and (m["output_tokens"] or 0) > 0:
            text_only_msgs.append({
                "uuid": m["uuid"],
                "timestamp": m["timestamp"],
                "output_tokens": m["output_tokens"],
                "thinking_tokens": m["thinking_tokens"],
                "cost": round(oc, 4),
            })

    # The total residual = total_msg_cost - attrib
    residual_total = round(total_msg_cost - attrib, 4)

    # Allocate residual into buckets:
    # 1) cache_read — entirely unattributed (no tool eats this)
    # 2) cache_creation — partial; conservative: assign all to residual bucket too
    # 3) thinking — output_tokens for thinking blocks; share of output
    # 4) text-only turn output — output for assistant messages with no tool_use
    # 5) the leftover "other input/output" — input + non-thinking output not attributed
    text_only_output_cost = sum(m["cost"] for m in text_only_msgs)
    # Sum of "thinking" sits inside output_total — to avoid double counting:
    # thinking is a portion of output_tokens, already included in oc above. We
    # split it out and reduce text_only_output_cost by overlapping share.
    # Build node list:
    def turn_label(ts, suffix=""):
        return f"turn @ {datetime_short(ts)}" + (f" · {suffix}" if suffix else "")

    children = []
    if cache_read_cost > 0.001:
        cache_read_turns.sort(key=lambda x: -x["cost"])
        kids = [{
            "name": turn_label(t["timestamp"], f"{fmt_int(t['cache_read_tokens'])} read · hit {int(t['hit_ratio']*100)}%"),
            "value": t["cost"],
            "kind": "residual_msg",
            "uuid": t["uuid"],
            "subtype": "cache_read",
            "model": t["model"],
            "cache_read_tokens": t["cache_read_tokens"],
            "input_tokens": t["input_tokens"],
            "hit_ratio": t["hit_ratio"],
            "timestamp": t["timestamp"],
        } for t in cache_read_turns[:50]]
        children.append({
            "name": "cache read (history replay)",
            "value": round(cache_read_cost, 4),
            "kind": "residual_cache",
            "summary": {
                "turns": len(cache_read_turns),
                "avg_hit_ratio": round(sum(t["hit_ratio"] for t in cache_read_turns) / max(1, len(cache_read_turns)), 4),
                "total_tokens": sum(t["cache_read_tokens"] for t in cache_read_turns),
            },
            "children": kids,
        })
    if cache_creation_cost > 0.001:
        cache_creation_turns.sort(key=lambda x: -x["cost"])
        kids = [{
            "name": turn_label(t["timestamp"], f"{fmt_int(t['cache_creation_tokens'])} written"),
            "value": t["cost"],
            "kind": "residual_msg",
            "uuid": t["uuid"],
            "subtype": "cache_creation",
            "model": t["model"],
            "cache_creation_tokens": t["cache_creation_tokens"],
            "timestamp": t["timestamp"],
        } for t in cache_creation_turns[:50]]
        children.append({
            "name": "cache creation",
            "value": round(cache_creation_cost, 4),
            "kind": "residual_cache",
            "summary": {
                "turns": len(cache_creation_turns),
                "total_tokens": sum(t["cache_creation_tokens"] for t in cache_creation_turns),
            },
            "children": kids,
        })
    if thinking_cost > 0.001:
        thinking_turns.sort(key=lambda x: -x["cost"])
        kids = [{
            "name": turn_label(t["timestamp"], f"{fmt_int(t['thinking_tokens'])} think · {int(t['ratio']*100)}% of out"),
            "value": t["cost"],
            "kind": "residual_msg",
            "uuid": t["uuid"],
            "subtype": "thinking",
            "model": t["model"],
            "thinking_tokens": t["thinking_tokens"],
            "output_tokens": t["output_tokens"],
            "ratio": t["ratio"],
            "timestamp": t["timestamp"],
        } for t in thinking_turns[:50]]
        children.append({
            "name": "thinking blocks",
            "value": round(thinking_cost, 4),
            "kind": "residual_thinking",
            "summary": {
                "turns": len(thinking_turns),
                "total_tokens": sum(t["thinking_tokens"] for t in thinking_turns),
                "avg_ratio": round(sum(t["ratio"] for t in thinking_turns) / max(1, len(thinking_turns)), 3),
            },
            "children": kids,
        })
    if text_only_output_cost > 0.001:
        text_only_msgs.sort(key=lambda x: -x["cost"])
        msg_kids = [{
            "name": turn_label(m['timestamp'], f"{fmt_int(m['output_tokens'])} out"),
            "value": m["cost"],
            "kind": "residual_msg",
            "uuid": m["uuid"],
            "subtype": "text_only",
            "output_tokens": m["output_tokens"],
            "thinking_tokens": m["thinking_tokens"],
            "timestamp": m["timestamp"],
        } for m in text_only_msgs[:50]]
        children.append({
            "name": "text-only turns (no tool)",
            "value": round(text_only_output_cost, 4),
            "kind": "residual_text",
            "summary": {"turns": len(text_only_msgs)},
            "children": msg_kids,
        })
    if input_total > 0.001:
        children.append({"name": "input tokens (user msgs + system)", "value": round(input_total, 4), "kind": "residual_input"})

    # Reconcile: ensure children sum ≤ residual_total. If component sum > residual,
    # scale or just label honestly (since pricing is approximated and tools eat some).
    comp_sum = sum(k["value"] for k in children)
    if comp_sum > residual_total + 0.01:
        # Show all components but flag the overlap (some tokens are double-counted
        # between cache and tool attribution). Add an "(attributed to tools)" subtractor.
        overlap = round(comp_sum - residual_total, 4)
        children.append({
            "name": "(− already counted under tools)",
            "value": -overlap,
            "kind": "residual_overlap",
        })

    return _json({
        "root": {
            "name": "(reasoning + cache)",
            "value": residual_total,
            "kind": "other",
            "children": children,
            "_breakdown": {
                "total_message_cost": round(total_msg_cost, 4),
                "attributed_to_tools": round(attrib, 4),
                "residual": residual_total,
            },
        },
    }, etag)


def datetime_short(ms):
    if ms is None:
        return "?"
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%m-%d %H:%M")


def fmt_int(n):
    if n is None:
        return "0"
    if n >= 1000:
        return f"{n/1000:.1f}k"
    return str(int(n))


# ----------------------------------------------------------------------
# Lazy file-tool sub-treemap (Read/Write/Edit/MultiEdit → file → call) under scope
@app.get("/api/treemap/file_tool")
def treemap_file_tool(
    req: Request,
    tool: str,                        # "Read" | "Write" | "Edit" | "MultiEdit"
    scope_kind: str,                  # "session" | "task"
    scope_id: str,
    project: str | None = None,
    max_files: int = 40,
    max_calls: int = 30,
):
    if tool not in ("Read", "Write", "Edit", "MultiEdit"):
        raise HTTPException(400, "tool must be Read|Write|Edit|MultiEdit")
    if scope_kind not in ("session", "task"):
        raise HTTPException(400, "scope_kind must be 'session' or 'task'")
    etag = _etag() + "|tmf|" + tool + "|" + scope_kind + ":" + scope_id + (project or "")
    if r := _maybe_304(req, etag):
        return r
    c = _conn()

    where = ["tool_name = ?"]
    params: list = [tool]
    if scope_kind == "session":
        where.append("session_id = ?"); params.append(scope_id)
    else:
        where.append(
            "(id = ? OR message_uuid IN ("
            "SELECT uuid FROM messages WHERE agent_id = "
            "(SELECT agent_id FROM tool_calls WHERE id = ?)))"
        )
        params.extend([scope_id, scope_id])
    if project:
        where.append("project = ?"); params.append(project)
    base_w = " WHERE " + " AND ".join(where)

    # Per-file rollup
    files = _rows(c, f"""
        SELECT COALESCE(file_path,'(unknown)') file_path,
               COUNT(*) calls,
               SUM(is_error) errors,
               SUM(user_modified) user_modified,
               SUM(truncated) truncated,
               ROUND(AVG(NULLIF(duration_ms,0)),0) avg_ms,
               SUM(result_bytes) total_bytes,
               SUM(result_lines) total_lines,
               ROUND(SUM(attributed_cost_usd),4) cost
        FROM tool_calls{base_w} AND attributed_cost_usd > 0
        GROUP BY file_path HAVING cost > 0
        ORDER BY cost DESC LIMIT ?""", tuple(params) + (max_files,))

    # Individual calls per file (top by cost)
    file_to_calls: dict[str, list[dict]] = {}
    if files:
        file_paths = [f["file_path"] for f in files]
        ph = ",".join(["?"] * len(file_paths))
        rows = _rows(c, f"""
            SELECT id, COALESCE(file_path,'(unknown)') file_path,
                   ROUND(attributed_cost_usd,4) cost,
                   timestamp, duration_ms,
                   result_bytes, result_lines,
                   is_error, user_modified, truncated, exit_code
            FROM tool_calls{base_w} AND COALESCE(file_path,'(unknown)') IN ({ph})
                                   AND attributed_cost_usd > 0
            ORDER BY attributed_cost_usd DESC LIMIT ?""",
            tuple(params) + tuple(file_paths) + (max_calls * len(file_paths),))
        for r in rows:
            fp = r["file_path"]
            file_to_calls.setdefault(fp, [])
            if len(file_to_calls[fp]) < max_calls:
                file_to_calls[fp].append(r)

    def short_file(fp: str) -> str:
        if not fp or fp == "(unknown)":
            return fp
        # Last 2 path segments
        parts = fp.split("/")
        return "/".join(parts[-2:]) if len(parts) > 2 else fp

    file_nodes = []
    for f in files:
        fp = f["file_path"]
        calls_under = file_to_calls.get(fp, [])
        call_kids = []
        for cl in calls_under:
            label_bits = []
            if cl["result_lines"]:
                label_bits.append(f"{cl['result_lines']}L")
            if cl["result_bytes"]:
                label_bits.append(fmt_bytes_short(cl["result_bytes"]))
            if cl["duration_ms"]:
                label_bits.append(f"{cl['duration_ms']}ms")
            label = "@" + datetime_short(cl["timestamp"]) + (" · " + " · ".join(label_bits) if label_bits else "")
            call_kids.append({
                "name": label,
                "value": cl["cost"],
                "kind": "file_call",
                "id": cl["id"],
                "is_error": cl["is_error"] or 0,
                "duration_ms": cl["duration_ms"],
                "result_bytes": cl["result_bytes"],
                "result_lines": cl["result_lines"],
                "user_modified": cl["user_modified"] or 0,
                "truncated": cl["truncated"] or 0,
                "timestamp": cl["timestamp"],
                "full_path": fp,
            })
        # File-level residual: total cost minus accounted calls (capped at max_calls)
        accounted = round(sum(c2["value"] for c2 in call_kids), 4)
        gap = round(f["cost"] - accounted, 4)
        if gap > 0.001:
            call_kids.append({"name": "(other calls)", "value": gap, "kind": "other"})
        file_nodes.append({
            "name": short_file(fp),
            "value": f["cost"],
            "kind": "file_path",
            "calls": f["calls"],
            "errors": f["errors"],
            "user_modified": f["user_modified"],
            "truncated": f["truncated"],
            "total_bytes": f["total_bytes"],
            "total_lines": f["total_lines"],
            "full_path": fp,
            "children": call_kids,
        })

    # Reconcile file-level cap vs actual scope total
    actual = c.execute(
        f"SELECT COALESCE(SUM(attributed_cost_usd),0) FROM tool_calls{base_w}",
        tuple(params),
    ).fetchone()[0] or 0
    file_sum = sum(f["value"] for f in file_nodes)
    gap = round(actual - file_sum, 4)
    if gap > 0.001:
        file_nodes.append({"name": "(other files)", "value": gap, "kind": "other"})

    return _json({
        "root": {
            "name": tool,
            "value": round(actual, 4),
            "kind": "tool",
            "children": file_nodes,
        },
    }, etag)


def fmt_bytes_short(n):
    if n is None:
        return ""
    if n >= 1_000_000:
        return f"{n/1_000_000:.1f}MB"
    if n >= 1000:
        return f"{n/1000:.1f}KB"
    return f"{n}B"


# ----------------------------------------------------------------------
# Lazy bash sub-treemap (Bash → program → subcommand → call) under a scope
@app.get("/api/treemap/bash")
def treemap_bash(
    req: Request,
    scope_kind: str,                  # "session" or "task"
    scope_id: str,
    project: str | None = None,
    since: str | None = None,
    until: str | None = None,
    max_programs: int = 30,
    max_subs: int = 20,
    max_calls: int = 30,
):
    if scope_kind not in ("session", "task"):
        raise HTTPException(400, "scope_kind must be 'session' or 'task'")
    etag = _etag() + "|tmb|" + scope_kind + ":" + scope_id + "|" + ",".join(
        filter(None, [project, since, until, str(max_programs), str(max_subs), str(max_calls)])
    )
    if r := _maybe_304(req, etag):
        return r
    c = _conn()

    # Build scope WHERE clause
    where = ["tool_name='Bash'"]
    params: list = []
    if scope_kind == "session":
        where.append("session_id = ?"); params.append(scope_id)
    else:
        where.append(
            "(id = ? OR message_uuid IN ("
            "SELECT uuid FROM messages WHERE agent_id = "
            "(SELECT agent_id FROM tool_calls WHERE id = ?)))"
        )
        params.extend([scope_id, scope_id])
    if project:
        where.append("project = ?"); params.append(project)
    if since:
        where.append("timestamp >= (strftime('%s', ?)*1000)"); params.append(since)
    if until:
        where.append("timestamp <= (strftime('%s', ?)*1000)"); params.append(until)
    base_w = " WHERE " + " AND ".join(where)

    # 1) Programs
    progs = _rows(c, f"""
        SELECT COALESCE(bash_program,'(unknown)') program,
               COUNT(*) calls,
               SUM(is_error) errors,
               ROUND(SUM(attributed_cost_usd),4) cost
        FROM tool_calls{base_w} AND attributed_cost_usd > 0
        GROUP BY bash_program HAVING cost > 0
        ORDER BY cost DESC LIMIT ?""", tuple(params) + (max_programs,))
    if not progs:
        return _json({"root": {"name": "Bash", "value": 0, "kind": "tool", "children": []}}, etag)

    # 2) Subcommands per program
    prog_names = [p["program"] for p in progs if p["program"] != "(unknown)"]
    subs_by_prog: dict[str, list[dict]] = {p: [] for p in [r["program"] for r in progs]}
    if prog_names:
        ph = ",".join(["?"] * len(prog_names))
        sub_rows = _rows(c, f"""
            SELECT bash_program program,
                   COALESCE(bash_subcommand,'(none)') subcommand,
                   COUNT(*) calls,
                   SUM(is_error) errors,
                   ROUND(SUM(attributed_cost_usd),4) cost
            FROM tool_calls{base_w} AND bash_program IN ({ph})
            GROUP BY bash_program, bash_subcommand HAVING cost > 0
            ORDER BY cost DESC""",
            tuple(params) + tuple(prog_names))
        for r in sub_rows:
            subs_by_prog.setdefault(r["program"], []).append(r)
        # Cap per program
        for p in subs_by_prog:
            subs_by_prog[p] = subs_by_prog[p][:max_subs]

    # 3) Individual calls per (program, subcommand) — only for the visible subcommands
    sub_keys: list[tuple[str, str]] = []
    for p, subs in subs_by_prog.items():
        for s in subs:
            sub_keys.append((p, s["subcommand"]))
    calls_by_subkey: dict[tuple[str, str], list[dict]] = {k: [] for k in sub_keys}
    if sub_keys:
        # Build batched OR clause; SQLite IN with tuples isn't standard, so emit
        # a chained predicate. Limit to top by cost across all sub_keys then bucket.
        # Simpler: one query, sort desc, distribute.
        q = f"""SELECT id, bash_program, COALESCE(bash_subcommand,'(none)') subcommand,
                       SUBSTR(bash_command,1,200) command,
                       attributed_cost_usd cost,
                       is_error, exit_code, duration_ms, result_bytes,
                       timestamp
                FROM tool_calls{base_w} AND attributed_cost_usd > 0
                ORDER BY attributed_cost_usd DESC LIMIT ?"""
        rows = _rows(c, q, tuple(params) + (max_calls * len(sub_keys),))
        for r in rows:
            key = (r["bash_program"], r["subcommand"])
            if key in calls_by_subkey and len(calls_by_subkey[key]) < max_calls:
                calls_by_subkey[key].append(r)

    # Assemble nested
    def n(name, value, kind, children=None, **extra):
        d = {"name": name, "value": value, "kind": kind}
        if children is not None:
            d["children"] = children
        d.update(extra)
        return d

    program_nodes = []
    for p in progs:
        prog_name = p["program"]
        sub_nodes = []
        for s in subs_by_prog.get(prog_name, []):
            disp_sub = s["subcommand"]
            label = prog_name if disp_sub == "(none)" else f"{prog_name} {disp_sub}"
            calls = calls_by_subkey.get((prog_name, disp_sub), [])
            call_nodes = []
            for cl in calls:
                call_nodes.append(n(
                    (cl["command"] or "")[:120],
                    round(cl["cost"], 4),
                    "bash_call",
                    id=cl["id"],
                    is_error=cl["is_error"] or 0,
                    exit_code=cl["exit_code"],
                    duration_ms=cl["duration_ms"],
                    result_bytes=cl["result_bytes"],
                    timestamp=cl["timestamp"],
                    full_command=cl["command"],
                ))
            sub_nodes.append(n(
                label, s["cost"], "bash_subcommand",
                calls=s["calls"], errors=s["errors"],
                children=call_nodes,
            ))
        program_nodes.append(n(
            prog_name, p["cost"], "bash_program",
            calls=p["calls"], errors=p["errors"],
            children=sub_nodes,
        ))

    # Reconcile against actual SUM in DB — adds (other) leaf if cap'd / filtered
    actual = c.execute(
        f"SELECT COALESCE(SUM(attributed_cost_usd),0) FROM tool_calls{base_w}",
        tuple(params),
    ).fetchone()[0] or 0
    program_sum = sum(p["value"] for p in program_nodes)
    gap = round(actual - program_sum, 4)
    if gap > 0.001:
        program_nodes.append({"name": "(other programs)", "value": gap, "kind": "other"})
    # Also reconcile within each program: sub_sum vs program cost
    for pn in program_nodes:
        if pn.get("kind") != "bash_program" or not pn.get("children"):
            continue
        sub_sum = sum(s["value"] for s in pn["children"])
        sg = round(pn["value"] - sub_sum, 4)
        if sg > 0.001:
            pn["children"].append({"name": "(other subcommands)", "value": sg, "kind": "other"})
        # And per subcommand: call_sum vs sub cost
        for sn in pn["children"]:
            if sn.get("kind") != "bash_subcommand" or not sn.get("children"):
                continue
            call_sum = sum(cs["value"] for cs in sn["children"])
            cg = round(sn["value"] - call_sum, 4)
            if cg > 0.001:
                sn["children"].append({"name": "(other calls)", "value": cg, "kind": "other"})

    return _json({
        "root": n("Bash", round(actual, 4), "tool", children=program_nodes),
    }, etag)


# ----------------------------------------------------------------------
# Bash analytics endpoints
@app.get("/api/bash/programs")
def bash_programs(
    req: Request,
    project: str | None = None,
    session_id: str | None = None,
    task_id: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = 30,
):
    etag = _etag() + "|bp|" + ",".join(filter(None, [project, session_id, task_id, since, until, str(limit)]))
    if r := _maybe_304(req, etag):
        return r
    c = _conn()
    _, _, tc_w, tc_p = _filters(project, session_id, task_id, "Bash", since, until)
    rows = _rows(c, f"""
        SELECT bash_program program,
               COALESCE(bash_category,'other') category,
               COUNT(*) calls,
               SUM(is_error) errors,
               SUM(bash_has_sudo) sudo_calls,
               ROUND(AVG(NULLIF(duration_ms,0)),0) avg_ms,
               ROUND(SUM(attributed_cost_usd),4) cost
        FROM tool_calls{tc_w} AND bash_program IS NOT NULL
        GROUP BY bash_program, category
        ORDER BY calls DESC LIMIT ?""", tc_p + (limit,))
    return _json({"programs": rows}, etag)


@app.get("/api/bash/program/{name}")
def bash_program_detail(
    name: str,
    req: Request,
    project: str | None = None,
    session_id: str | None = None,
    task_id: str | None = None,
    since: str | None = None,
    until: str | None = None,
):
    etag = _etag() + "|bpd|" + name + "|" + ",".join(filter(None, [project, session_id, task_id, since, until]))
    if r := _maybe_304(req, etag):
        return r
    c = _conn()
    _, _, tc_w, tc_p = _filters(project, session_id, task_id, "Bash", since, until)
    extra = " AND bash_program = ?"
    summary = c.execute(f"""
        SELECT COUNT(*) calls,
               SUM(is_error) errors,
               SUM(bash_has_sudo) sudo_calls,
               SUM(bash_pipe_count > 0) pipes_used,
               ROUND(AVG(NULLIF(duration_ms,0)),0) avg_ms,
               ROUND(SUM(attributed_cost_usd),4) cost
        FROM tool_calls{tc_w}{extra}""", tc_p + (name,)).fetchone()
    subs = _rows(c, f"""
        SELECT COALESCE(bash_subcommand,'(none)') subcommand,
               COUNT(*) calls,
               SUM(is_error) errors,
               ROUND(SUM(attributed_cost_usd),4) cost
        FROM tool_calls{tc_w}{extra}
        GROUP BY bash_subcommand
        ORDER BY calls DESC LIMIT 30""", tc_p + (name,))
    samples = _rows(c, f"""
        SELECT id, timestamp, is_error, exit_code,
               SUBSTR(bash_command,1,200) command,
               ROUND(attributed_cost_usd,4) cost,
               duration_ms
        FROM tool_calls{tc_w}{extra}
        ORDER BY attributed_cost_usd DESC LIMIT 30""", tc_p + (name,))
    return _json({"summary": dict(summary) if summary else {}, "subcommands": subs, "samples": samples}, etag)


@app.get("/api/bash/categories")
def bash_categories(
    req: Request,
    project: str | None = None,
    session_id: str | None = None,
    task_id: str | None = None,
    since: str | None = None,
    until: str | None = None,
):
    etag = _etag() + "|bc|" + ",".join(filter(None, [project, session_id, task_id, since, until]))
    if r := _maybe_304(req, etag):
        return r
    c = _conn()
    _, _, tc_w, tc_p = _filters(project, session_id, task_id, "Bash", since, until)
    rows = _rows(c, f"""
        SELECT COALESCE(bash_category,'other') category,
               COUNT(*) calls,
               SUM(is_error) errors,
               ROUND(SUM(attributed_cost_usd),4) cost
        FROM tool_calls{tc_w}
        GROUP BY category ORDER BY calls DESC""", tc_p)
    return _json({"categories": rows}, etag)


# ----------------------------------------------------------------------
# Hierarchical breakdown (lazy-loaded tree)
@app.get("/api/breakdown/projects")
def bd_projects(req: Request, since: str | None = None, until: str | None = None):
    etag = _etag() + "|bdp|" + ",".join(filter(None, [since, until]))
    if r := _maybe_304(req, etag):
        return r
    c = _conn()
    msg_w, msg_p, _, _ = _filters(None, None, None, None, since, until)
    rows = _rows(c, f"""
        SELECT project,
               COUNT(DISTINCT session_id) sessions,
               COUNT(*) msgs,
               ROUND(SUM(cost_usd),4) cost,
               SUM(is_compact_summary) compactions,
               SUM(is_api_error) errors
        FROM messages{msg_w} GROUP BY project ORDER BY cost DESC""", msg_p)
    return _json({"items": rows}, etag)


@app.get("/api/breakdown/sessions")
def bd_sessions(req: Request, project: str, since: str | None = None, until: str | None = None):
    etag = _etag() + "|bds|" + ",".join(filter(None, [project, since, until]))
    if r := _maybe_304(req, etag):
        return r
    c = _conn()
    where = ["project = ?"]
    params: list = [project]
    if since:
        where.append("started_at >= (strftime('%s', ?)*1000)"); params.append(since)
    if until:
        where.append("started_at <= (strftime('%s', ?)*1000)"); params.append(until)
    rows = _rows(c, f"""
        SELECT session_id, started_at, ended_at,
               message_count, tool_call_count,
               compaction_count, error_count,
               ROUND(total_cost_usd,4) cost,
               ROUND(cache_hit_ratio,3) cache_hit
        FROM sessions WHERE {' AND '.join(where)}
        ORDER BY total_cost_usd DESC""", tuple(params))
    return _json({"items": rows}, etag)


@app.get("/api/breakdown/session/{session_id}")
def bd_session_children(session_id: str, req: Request):
    """Children of a session = (a) tasks rooted in this session, (b) tools called
    directly in this session that are NOT inside any task."""
    etag = _etag() + "|bdsc|" + session_id
    if r := _maybe_304(req, etag):
        return r
    c = _conn()
    tasks = _rows(c, """
        SELECT root_tool_use_id id, agent_type, description,
               started_at, duration_ms, message_count, tool_call_count,
               ROUND(total_cost_usd,4) cost, is_error
        FROM tasks WHERE session_id = ?
        ORDER BY total_cost_usd DESC""", (session_id,))
    # Tools NOT inside a sub-agent: tool_calls in messages where is_sidechain=0
    direct_tools = _rows(c, """
        SELECT tc.tool_name, COUNT(*) calls,
               SUM(tc.is_error) errors,
               ROUND(SUM(tc.attributed_cost_usd),4) cost
        FROM tool_calls tc
        JOIN messages m ON m.uuid = tc.message_uuid
        WHERE tc.session_id = ? AND COALESCE(m.is_sidechain,0) = 0
        GROUP BY tc.tool_name ORDER BY cost DESC""", (session_id,))
    return _json({"tasks": tasks, "tools": direct_tools}, etag)


@app.get("/api/breakdown/task/{task_id}")
def bd_task_children(task_id: str, req: Request):
    """Children of a task = tools used inside it (root + descendants by agent_id)."""
    etag = _etag() + "|bdtc|" + task_id
    if r := _maybe_304(req, etag):
        return r
    c = _conn()
    rows = _rows(c, """
        SELECT tool_name, COUNT(*) calls,
               SUM(is_error) errors,
               SUM(interrupted) interrupted,
               ROUND(AVG(NULLIF(duration_ms,0)),0) avg_ms,
               ROUND(SUM(attributed_cost_usd),4) cost
        FROM tool_calls
        WHERE id = ?
           OR message_uuid IN (
              SELECT uuid FROM messages
              WHERE agent_id = (SELECT agent_id FROM tool_calls WHERE id = ?)
           )
        GROUP BY tool_name ORDER BY cost DESC""", (task_id, task_id))
    return _json({"tools": rows}, etag)


@app.get("/api/breakdown/calls")
def bd_calls(
    req: Request,
    session_id: str | None = None,
    task_id: str | None = None,
    tool: str | None = None,
    direct_only: int = 0,
    limit: int = 200,
):
    """Individual tool_calls under a (session|task) × tool."""
    etag = _etag() + "|bdc|" + ",".join(filter(None, [session_id, task_id, tool, str(direct_only)]))
    if r := _maybe_304(req, etag):
        return r
    c = _conn()
    where: list[str] = []
    params: list = []
    if tool:
        where.append("tc.tool_name = ?"); params.append(tool)
    if task_id:
        where.append(
            "(tc.id = ? OR tc.message_uuid IN ("
            "SELECT uuid FROM messages WHERE agent_id = "
            "(SELECT agent_id FROM tool_calls WHERE id = ?)))"
        )
        params.extend([task_id, task_id])
    elif session_id:
        where.append("tc.session_id = ?"); params.append(session_id)
        if direct_only:
            where.append(
                "tc.message_uuid IN (SELECT uuid FROM messages "
                "WHERE session_id = ? AND COALESCE(is_sidechain,0)=0)"
            )
            params.append(session_id)
    where_sql = (" WHERE " + " AND ".join(where)) if where else ""
    rows = _rows(c, f"""
        SELECT tc.id, tc.tool_name, tc.timestamp, tc.duration_ms,
               tc.is_error, tc.exit_code,
               tc.bash_command, tc.file_path, tc.search_pattern,
               tc.web_url, tc.web_query, tc.agent_subtype, tc.agent_description,
               tc.result_bytes, tc.result_lines,
               ROUND(tc.attributed_cost_usd,4) cost,
               tc.attributed_input_tokens in_tokens,
               tc.attributed_output_tokens out_tokens
        FROM tool_calls tc{where_sql}
        ORDER BY tc.attributed_cost_usd DESC LIMIT ?""",
        tuple(params) + (limit,))
    return _json({"calls": rows}, etag)


# ----------------------------------------------------------------------
# Static frontend
@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")


app.mount("/web", StaticFiles(directory=str(WEB_DIR)), name="web")


# ----------------------------------------------------------------------
# Watcher: filesystem events via watchdog with 5s debounce
_watcher_started = False
_pending_change = threading.Event()
_watcher_lock = threading.Lock()
_initial_done = threading.Event()


def start_watcher() -> None:
    global _watcher_started
    with _watcher_lock:
        if _watcher_started:
            return
        _watcher_started = True

    threading.Thread(target=_initial_ingest, daemon=True).start()
    threading.Thread(target=_debounce_loop, daemon=True).start()
    threading.Thread(target=_observe_fs, daemon=True).start()


def _initial_ingest() -> None:
    from .ingest import ingest_all
    from .aggregate import rebuild_all
    try:
        ingest_all()
        rebuild_all()
    except Exception as e:
        print(f"initial ingest failed: {e}")
    finally:
        _initial_done.set()


def _debounce_loop() -> None:
    """Wait for a change signal, debounce 5s, then ingest."""
    from .ingest import ingest_all
    from .aggregate import rebuild_all
    while True:
        _pending_change.wait()
        # debounce window: gather more events for 5s
        time.sleep(5)
        _pending_change.clear()
        try:
            stats = ingest_all()
            if stats["files"] > 0:
                rebuild_all()
        except Exception as e:
            print(f"watcher ingest failed: {e}")


def _observe_fs() -> None:
    try:
        from watchdog.observers import Observer
        from watchdog.events import FileSystemEventHandler
    except ImportError:
        # Fall back to polling if watchdog missing
        while True:
            time.sleep(30)
            _pending_change.set()

    class Handler(FileSystemEventHandler):
        def on_any_event(self, event):
            if not event.is_directory and str(event.src_path).endswith(".jsonl"):
                _pending_change.set()

    obs = Observer()
    if PROJECTS_DIR.exists():
        obs.schedule(Handler(), str(PROJECTS_DIR), recursive=True)
        obs.start()
    # Block forever on this thread
    while True:
        time.sleep(3600)
