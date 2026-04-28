"""Pure-Python analytics queries — single source of SQL for FastAPI + MCP + CLI.

All functions take a `filters: dict` with optional keys
{project, session_id, task_id, tool, since, until} and return JSON-serializable
plain Python (dicts / lists). No FastAPI types here.
"""

from __future__ import annotations

import math
import sqlite3
from typing import Any

from .db import connect, init_schema

Filters = dict[str, Any]
_LIMIT_CAP = 100


def _conn() -> sqlite3.Connection:
    c = connect()
    init_schema(c)
    return c


def _rows(c: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    return [dict(r) for r in c.execute(sql, params).fetchall()]


def _build_filters(f: Filters | None) -> tuple[str, tuple, str, tuple]:
    """Return (msg_clause, msg_params, tc_clause, tc_params)."""
    f = f or {}
    project = f.get("project")
    session_id = f.get("session_id")
    task_id = f.get("task_id")
    tool = f.get("tool")
    since = f.get("since")
    until = f.get("until")
    msg_w: list[str] = []
    msg_p: list = []
    tc_w: list[str] = []
    tc_p: list = []
    if project:
        msg_w.append("project = ?")
        msg_p.append(project)
        tc_w.append("project = ?")
        tc_p.append(project)
    if session_id:
        msg_w.append("session_id = ?")
        msg_p.append(session_id)
        tc_w.append("session_id = ?")
        tc_p.append(session_id)
    if task_id:
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
        tc_w.append("tool_name = ?")
        tc_p.append(tool)
    if since:
        s_str = str(since)
        ts = int(s_str) if s_str.isdigit() else None
        if ts is None:
            msg_w.append("timestamp >= (strftime('%s', ?)*1000)")
            msg_p.append(s_str)
            tc_w.append("timestamp >= (strftime('%s', ?)*1000)")
            tc_p.append(s_str)
        else:
            msg_w.append("timestamp >= ?")
            msg_p.append(ts)
            tc_w.append("timestamp >= ?")
            tc_p.append(ts)
    if until:
        u_str = str(until)
        ts = int(u_str) if u_str.isdigit() else None
        if ts is None:
            msg_w.append("timestamp <= (strftime('%s', ?)*1000)")
            msg_p.append(u_str)
            tc_w.append("timestamp <= (strftime('%s', ?)*1000)")
            tc_p.append(u_str)
        else:
            msg_w.append("timestamp <= ?")
            msg_p.append(ts)
            tc_w.append("timestamp <= ?")
            tc_p.append(ts)
    msg_clause = (" WHERE " + " AND ".join(msg_w)) if msg_w else ""
    tc_clause = (" WHERE " + " AND ".join(tc_w)) if tc_w else ""
    return msg_clause, tuple(msg_p), tc_clause, tuple(tc_p)


def _filters_no_tool(f: Filters | None) -> tuple[str, tuple]:
    f = dict(f or {})
    f.pop("tool", None)
    msg_w, msg_p, _, _ = _build_filters(f)
    return msg_w, msg_p


def _delta_pct(cur: float, prev: float) -> float | None:
    if prev is None or prev == 0:
        return None
    return round((cur - prev) / prev, 4)


# ---------- overview with period-over-period ---------------------------


def overview(filters: Filters | None = None) -> dict:
    c = _conn()
    msg_w, msg_p, tc_w, tc_p = _build_filters(filters)
    msg_w_nt, msg_p_nt = _filters_no_tool(filters)

    spend_row = c.execute(
        f"""SELECT
              COALESCE(SUM(CASE WHEN timestamp >= (strftime('%s','now','start of day')*1000)
                                THEN cost_usd ELSE 0 END),0) today,
              COALESCE(SUM(CASE WHEN timestamp >= (strftime('%s','now','-1 day','start of day')*1000)
                                AND timestamp < (strftime('%s','now','start of day')*1000)
                                THEN cost_usd ELSE 0 END),0) yesterday,
              COALESCE(SUM(CASE WHEN timestamp >= (strftime('%s','now','-7 days')*1000)
                                THEN cost_usd ELSE 0 END),0) last_7d,
              COALESCE(SUM(CASE WHEN timestamp >= (strftime('%s','now','-14 days')*1000)
                                AND timestamp < (strftime('%s','now','-7 days')*1000)
                                THEN cost_usd ELSE 0 END),0) prev_7d,
              COALESCE(SUM(CASE WHEN timestamp >= (strftime('%s','now','-30 days')*1000)
                                THEN cost_usd ELSE 0 END),0) last_30d,
              COALESCE(SUM(CASE WHEN timestamp >= (strftime('%s','now','-60 days')*1000)
                                AND timestamp < (strftime('%s','now','-30 days')*1000)
                                THEN cost_usd ELSE 0 END),0) prev_30d,
              COALESCE(SUM(cost_usd),0) all_time,
              COUNT(*) msgs,
              COUNT(DISTINCT session_id) sessions,
              COUNT(DISTINCT project) projects,
              COALESCE(SUM(input_tokens),0) input,
              COALESCE(SUM(output_tokens),0) output,
              COALESCE(SUM(cache_creation),0) cache_creation,
              COALESCE(SUM(cache_read),0) cache_read,
              COALESCE(SUM(thinking_tokens),0) thinking,
              COALESCE(SUM(is_compact_summary),0) compactions,
              COALESCE(SUM(is_api_error),0) api_errors
           FROM messages{msg_w_nt}""",
        msg_p_nt,
    ).fetchone()

    cache_hit = None
    cr = spend_row["cache_read"]
    it = spend_row["input"]
    if cr + it > 0:
        cache_hit = round(cr / (cr + it), 4)

    top_projects = _rows(
        c,
        f"""
        SELECT project, ROUND(SUM(cost_usd),4) cost, COUNT(*) msgs
        FROM messages{msg_w_nt}
        GROUP BY project ORDER BY cost DESC LIMIT 8""",
        msg_p_nt,
    )
    top_tools = _rows(
        c,
        f"""
        SELECT tool_name, COUNT(*) calls, ROUND(SUM(attributed_cost_usd),4) cost
        FROM tool_calls{tc_w}
        GROUP BY tool_name ORDER BY cost DESC LIMIT 10""",
        tc_p,
    )
    sparkline = _rows(
        c,
        f"""
        SELECT DATE(timestamp/1000,'unixepoch') day, ROUND(SUM(cost_usd),4) cost
        FROM messages{msg_w_nt}
          {"AND" if msg_w_nt else "WHERE"} timestamp IS NOT NULL
        GROUP BY day ORDER BY day DESC LIMIT 30""",
        msg_p_nt,
    )

    return {
        "spend": {
            "today": round(spend_row["today"], 4),
            "yesterday": round(spend_row["yesterday"], 4),
            "delta_today_pct": _delta_pct(spend_row["today"], spend_row["yesterday"]),
            "last_7d": round(spend_row["last_7d"], 4),
            "prev_7d": round(spend_row["prev_7d"], 4),
            "delta_7d_pct": _delta_pct(spend_row["last_7d"], spend_row["prev_7d"]),
            "last_30d": round(spend_row["last_30d"], 4),
            "prev_30d": round(spend_row["prev_30d"], 4),
            "delta_30d_pct": _delta_pct(spend_row["last_30d"], spend_row["prev_30d"]),
            "all_time": round(spend_row["all_time"], 4),
        },
        "counts": {
            "messages": spend_row["msgs"],
            "sessions": spend_row["sessions"],
            "projects": spend_row["projects"],
        },
        "tokens": {
            "input": spend_row["input"],
            "output": spend_row["output"],
            "cache_creation": spend_row["cache_creation"],
            "cache_read": spend_row["cache_read"],
            "thinking": spend_row["thinking"],
        },
        "cache_hit_ratio": cache_hit,
        "health": {
            "compactions": spend_row["compactions"],
            "api_errors": spend_row["api_errors"],
        },
        "top_projects": top_projects,
        "top_tools": top_tools,
        "sparkline": list(reversed(sparkline)),
    }


# ---------- top costs --------------------------------------------------

_TOP_COSTS_BY = {
    "tool": ("tool_calls", "tool_name", "attributed_cost_usd", "tc"),
    "project": ("messages", "project", "cost_usd", "msg"),
    "session": ("messages", "session_id", "cost_usd", "msg"),
    "task": ("tasks", "root_tool_use_id", "total_cost_usd", "task"),
    "file": ("tool_calls", "file_path", "attributed_cost_usd", "tc_file"),
    "bash_program": ("tool_calls", "bash_program", "attributed_cost_usd", "tc_bash"),
    "bash_subcommand": (
        "tool_calls",
        "COALESCE(bash_subcommand,'(none)')",
        "attributed_cost_usd",
        "tc_bash",
    ),
    "model": ("messages", "model", "cost_usd", "msg"),
}


def top_costs(by: str, limit: int = 20, filters: Filters | None = None) -> list[dict]:
    if by not in _TOP_COSTS_BY:
        return [{"error": f"invalid `by`. allowed: {sorted(_TOP_COSTS_BY)}"}]
    limit = max(1, min(int(limit), _LIMIT_CAP))
    table, key, cost_col, mode = _TOP_COSTS_BY[by]
    c = _conn()
    msg_w, msg_p, tc_w, tc_p = _build_filters(filters)

    if mode == "tc":
        sql = f"""SELECT {key} AS key, COUNT(*) calls, SUM(is_error) errors,
                         ROUND(SUM({cost_col}),4) cost
                  FROM tool_calls{tc_w}
                  GROUP BY {key} ORDER BY cost DESC LIMIT ?"""
        return _rows(c, sql, tc_p + (limit,))

    if mode == "tc_file":
        clause = (tc_w + " AND " if tc_w else " WHERE ") + (
            "file_path IS NOT NULL AND tool_name IN ('Read','Edit','Write','MultiEdit')"
        )
        sql = f"""SELECT {key} AS key, COUNT(*) calls, SUM(is_error) errors,
                         ROUND(SUM({cost_col}),4) cost,
                         COUNT(DISTINCT session_id) sessions
                  FROM tool_calls{clause}
                  GROUP BY {key} ORDER BY cost DESC LIMIT ?"""
        return _rows(c, sql, tc_p + (limit,))

    if mode == "tc_bash":
        clause = (tc_w + " AND " if tc_w else " WHERE ") + (
            "tool_name='Bash' AND bash_program IS NOT NULL"
        )
        sql = f"""SELECT {key} AS key, COUNT(*) calls, SUM(is_error) errors,
                         ROUND(SUM({cost_col}),4) cost
                  FROM tool_calls{clause}
                  GROUP BY {key} ORDER BY cost DESC LIMIT ?"""
        return _rows(c, sql, tc_p + (limit,))

    if mode == "msg":
        sql = f"""SELECT {key} AS key, COUNT(*) msgs,
                         COALESCE(SUM(input_tokens),0) input,
                         COALESCE(SUM(output_tokens),0) output,
                         COALESCE(SUM(cache_read),0) cache_read,
                         ROUND(SUM({cost_col}),4) cost
                  FROM messages{msg_w}
                  GROUP BY {key} ORDER BY cost DESC LIMIT ?"""
        return _rows(c, sql, msg_p + (limit,))

    if mode == "task":
        # No project/since filter on tasks table directly -- delegate via session join.
        msg_w_nt, msg_p_nt = _filters_no_tool(filters)
        if msg_w_nt:
            sql = f"""SELECT t.root_tool_use_id key, t.agent_type,
                             SUBSTR(t.description,1,80) description,
                             t.message_count msgs, t.tool_call_count tools,
                             ROUND(t.total_cost_usd,4) cost, t.project
                      FROM tasks t
                      WHERE t.session_id IN (SELECT DISTINCT session_id FROM messages{msg_w_nt})
                      ORDER BY t.total_cost_usd DESC LIMIT ?"""
            return _rows(c, sql, msg_p_nt + (limit,))
        sql = """SELECT root_tool_use_id key, agent_type,
                        SUBSTR(description,1,80) description,
                        message_count msgs, tool_call_count tools,
                        ROUND(total_cost_usd,4) cost, project
                 FROM tasks ORDER BY total_cost_usd DESC LIMIT ?"""
        return _rows(c, sql, (limit,))

    return []


# ---------- session detail (deep dive) ---------------------------------


def session_detail(session_id: str) -> dict:
    c = _conn()
    sess = c.execute("SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
    if not sess:
        return {"error": f"session not found: {session_id}"}
    timeline = _rows(
        c,
        """
        SELECT timestamp, role, model, input_tokens, output_tokens,
               cache_creation, cache_read, thinking_tokens, cost_usd,
               is_compact_summary, is_api_error
        FROM messages WHERE session_id=? AND timestamp IS NOT NULL
        ORDER BY timestamp""",
        (session_id,),
    )
    tool_breakdown = _rows(
        c,
        """
        SELECT tool_name, COUNT(*) calls,
               ROUND(SUM(attributed_cost_usd),4) cost,
               SUM(is_error) errors,
               ROUND(AVG(NULLIF(duration_ms,0)),0) avg_ms
        FROM tool_calls WHERE session_id=?
        GROUP BY tool_name ORDER BY cost DESC""",
        (session_id,),
    )
    bash_breakdown = _rows(
        c,
        """
        SELECT COALESCE(bash_program,'(unknown)') program,
               COALESCE(bash_subcommand,'(none)') subcommand,
               COUNT(*) calls, SUM(is_error) errors,
               ROUND(SUM(attributed_cost_usd),4) cost
        FROM tool_calls WHERE session_id=? AND tool_name='Bash'
        GROUP BY program, subcommand ORDER BY cost DESC LIMIT 30""",
        (session_id,),
    )
    file_activity_rows = _rows(
        c,
        """
        SELECT file_path,
               SUM(CASE WHEN tool_name='Read' THEN 1 ELSE 0 END) reads,
               SUM(CASE WHEN tool_name='Edit' THEN 1 ELSE 0 END) edits,
               SUM(CASE WHEN tool_name='Write' THEN 1 ELSE 0 END) writes,
               ROUND(SUM(attributed_cost_usd),4) cost
        FROM tool_calls
        WHERE session_id=? AND file_path IS NOT NULL
              AND tool_name IN ('Read','Edit','Write','MultiEdit')
        GROUP BY file_path ORDER BY (reads+edits+writes) DESC LIMIT 30""",
        (session_id,),
    )
    # reasoning + cache aggregate for this session
    rc_row = c.execute(
        """
        SELECT COALESCE(SUM(input_tokens),0) inp,
               COALESCE(SUM(output_tokens),0) out,
               COALESCE(SUM(cache_read),0) cr,
               COALESCE(SUM(cache_creation),0) cc,
               COALESCE(SUM(thinking_tokens),0) think,
               ROUND(SUM(cost_usd),4) cost
        FROM messages WHERE session_id=?""",
        (session_id,),
    ).fetchone()
    inp = rc_row["inp"]
    cr = rc_row["cr"]
    out = rc_row["out"]
    cc = rc_row["cc"]
    rc_summary = {
        "input_tokens": inp,
        "output_tokens": out,
        "cache_read": cr,
        "cache_creation": cc,
        "thinking_tokens": rc_row["think"],
        "cost": rc_row["cost"],
        "cache_hit_ratio": round(cr / (cr + inp), 4) if (cr + inp) else None,
        "thinking_pct_of_output": round(rc_row["think"] / out, 4) if out else None,
        "cache_creation_pct": round(cc / (inp + cc), 4) if (inp + cc) else None,
    }
    return {
        "session": dict(sess),
        "timeline": timeline,
        "tool_breakdown": tool_breakdown,
        "bash_breakdown": bash_breakdown,
        "file_activity": file_activity_rows,
        "reasoning_cache": rc_summary,
    }


# ---------- reasoning + cache aggregations -----------------------------


def reasoning_cache(group_by: str = "model", filters: Filters | None = None) -> list[dict]:
    if group_by not in {"model", "session", "project", "day"}:
        return [{"error": "group_by must be model|session|project|day"}]
    c = _conn()
    msg_w_nt, msg_p_nt = _filters_no_tool(filters)
    if group_by == "day":
        key_expr = "DATE(timestamp/1000,'unixepoch')"
    elif group_by == "session":
        key_expr = "session_id"
    elif group_by == "project":
        key_expr = "project"
    else:
        key_expr = "model"

    sql = f"""SELECT {key_expr} AS key,
                COALESCE(SUM(input_tokens),0) inp,
                COALESCE(SUM(output_tokens),0) out,
                COALESCE(SUM(cache_read),0) cr,
                COALESCE(SUM(cache_creation),0) cc,
                COALESCE(SUM(thinking_tokens),0) think,
                ROUND(SUM(cost_usd),4) cost
              FROM messages{msg_w_nt}
              GROUP BY {key_expr}
              HAVING SUM(cost_usd) > 0
              ORDER BY cost DESC LIMIT ?"""
    raw = _rows(c, sql, msg_p_nt + (_LIMIT_CAP,))
    out = []
    for r in raw:
        inp = r["inp"]
        cr = r["cr"]
        ot = r["out"]
        cc = r["cc"]
        out.append(
            {
                "key": r["key"],
                "cost": r["cost"],
                "input_tokens": inp,
                "output_tokens": ot,
                "cache_read": cr,
                "cache_creation": cc,
                "thinking_tokens": r["think"],
                "cache_hit_ratio": round(cr / (cr + inp), 4) if (cr + inp) else None,
                "cache_creation_pct": round(cc / (inp + cc), 4) if (inp + cc) else None,
                "thinking_pct_of_output": round(r["think"] / ot, 4) if ot else None,
            }
        )
    return out


# ---------- detectors --------------------------------------------------


def duplicate_reads(filters: Filters | None = None, min_dups: int = 2) -> list[dict]:
    min_dups = max(1, min(int(min_dups), 50))
    c = _conn()
    _, _, tc_w, tc_p = _build_filters(filters)
    extra = "AND file_path IS NOT NULL AND tool_name IN ('Read','Edit','Write','MultiEdit')"
    where = (tc_w + " " + extra) if tc_w else (" WHERE " + extra.lstrip("AND ").lstrip())
    # NB: using LSP ROWS ... PRECEDING to get cumulative edit count groups
    sql = f"""WITH ord AS (
                SELECT session_id, file_path, tool_name, timestamp,
                  SUM(CASE WHEN tool_name IN ('Edit','Write','MultiEdit') THEN 1 ELSE 0 END)
                    OVER (PARTITION BY session_id, file_path
                          ORDER BY timestamp
                          ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS edits_so_far
                FROM tool_calls{where})
              SELECT session_id, file_path,
                SUM(CASE WHEN tool_name='Read' THEN 1 ELSE 0 END) reads,
                COUNT(DISTINCT edits_so_far) edit_groups,
                SUM(CASE WHEN tool_name='Read' THEN 1 ELSE 0 END)
                  - COUNT(DISTINCT edits_so_far) AS dup_reads
              FROM ord GROUP BY session_id, file_path
              HAVING dup_reads >= ?
              ORDER BY dup_reads DESC LIMIT ?"""
    return _rows(c, sql, tc_p + (min_dups, _LIMIT_CAP))


def bash_retries(filters: Filters | None = None, window_s: int = 60) -> list[dict]:
    window_s = max(1, min(int(window_s), 3600))
    c = _conn()
    _, _, tc_w, tc_p = _build_filters(filters)
    extra = "AND tool_name='Bash' AND bash_command IS NOT NULL"
    where = (tc_w + " " + extra) if tc_w else (" WHERE " + extra.lstrip("AND ").lstrip())
    sql = f"""WITH lagged AS (
                SELECT session_id, bash_command, timestamp, is_error, exit_code,
                  LAG(bash_command) OVER (PARTITION BY session_id ORDER BY timestamp) prev_cmd,
                  LAG(timestamp)    OVER (PARTITION BY session_id ORDER BY timestamp) prev_ts
                FROM tool_calls{where})
              SELECT session_id, bash_command,
                COUNT(*) retries, SUM(is_error) errors, MAX(exit_code) last_exit
              FROM lagged
              WHERE bash_command = prev_cmd
                AND (timestamp - prev_ts) < ? * 1000
              GROUP BY session_id, bash_command
              HAVING retries >= 2 AND errors >= 1
              ORDER BY errors DESC, retries DESC LIMIT ?"""
    return _rows(c, sql, tc_p + (window_s, _LIMIT_CAP))


def error_chains(
    filters: Filters | None = None, min_n: int = 5, min_rate: float = 0.2
) -> list[dict]:
    min_n = max(1, min(int(min_n), 1000))
    min_rate = max(0.0, min(float(min_rate), 1.0))
    c = _conn()
    _, _, tc_w, tc_p = _build_filters(filters)
    sql = f"""WITH lagged AS (
                SELECT tool_name AS next_tool, is_error, session_id,
                  LAG(tool_name) OVER (PARTITION BY session_id ORDER BY timestamp, id) prev_tool
                FROM tool_calls{tc_w})
              SELECT prev_tool, next_tool, COUNT(*) n, SUM(is_error) errs,
                ROUND(1.0*SUM(is_error)/COUNT(*),3) err_rate
              FROM lagged WHERE prev_tool IS NOT NULL
              GROUP BY prev_tool, next_tool
              HAVING n >= ? AND err_rate >= ?
              ORDER BY errs DESC LIMIT ?"""
    return _rows(c, sql, tc_p + (min_n, min_rate, _LIMIT_CAP))


def cost_outliers(filters: Filters | None = None, z_min: float = 2.0) -> list[dict]:
    """Sessions whose cost is z>=z_min vs project mean. SQLite lacks STDDEV → Python."""
    z_min = max(0.5, min(float(z_min), 5.0))
    c = _conn()
    msg_w_nt, msg_p_nt = _filters_no_tool(filters)
    # Filter sessions by their own project/timestamp via the messages join only when filters present.
    if msg_w_nt:
        rows = _rows(
            c,
            f"""
            SELECT s.session_id, s.project, s.total_cost_usd cost, s.message_count msgs
            FROM sessions s
            WHERE s.total_cost_usd > 0
              AND s.session_id IN (SELECT DISTINCT session_id FROM messages{msg_w_nt})""",
            msg_p_nt,
        )
    else:
        rows = _rows(
            c,
            """
            SELECT session_id, project, total_cost_usd cost, message_count msgs
            FROM sessions WHERE total_cost_usd > 0""",
        )
    by_proj: dict[str, list[dict]] = {}
    for r in rows:
        by_proj.setdefault(r["project"] or "(none)", []).append(r)
    out: list[dict] = []
    for proj, items in by_proj.items():
        if len(items) < 3:
            continue
        costs = [x["cost"] for x in items]
        mu = sum(costs) / len(costs)
        var = sum((x - mu) ** 2 for x in costs) / len(costs)
        sigma = math.sqrt(var)
        if sigma == 0:
            continue
        for r in items:
            z = (r["cost"] - mu) / sigma
            if z >= z_min:
                out.append(
                    {
                        "session_id": r["session_id"],
                        "project": proj,
                        "cost": round(r["cost"], 4),
                        "msgs": r["msgs"],
                        "project_mean_cost": round(mu, 4),
                        "z_score": round(z, 2),
                    }
                )
    out.sort(key=lambda r: r["z_score"], reverse=True)
    return out[:_LIMIT_CAP]


def compaction_root(session_id: str | None = None, filters: Filters | None = None) -> list[dict]:
    """Largest output_tokens+cache_creation message in the 10-min window before each compaction."""
    c = _conn()
    msg_w_nt, msg_p_nt = _filters_no_tool(filters)
    where_extra = "is_compact_summary = 1"
    if session_id:
        where_extra += " AND session_id = ?"
    if msg_w_nt:
        comp_where = msg_w_nt + " AND " + where_extra
    else:
        comp_where = " WHERE " + where_extra
    comp_params = list(msg_p_nt)
    if session_id:
        comp_params.append(session_id)
    sql = f"""WITH compactions AS (
                SELECT session_id, timestamp ct
                FROM messages{comp_where})
              SELECT m.session_id, m.uuid, m.model,
                COALESCE(m.output_tokens,0) output_tokens,
                COALESCE(m.cache_creation,0) cache_creation,
                COALESCE(m.thinking_tokens,0) thinking_tokens,
                ROUND(m.cost_usd,4) cost_usd,
                m.timestamp msg_ts, c.ct compact_ts
              FROM compactions c
              JOIN messages m ON m.session_id = c.session_id
              WHERE m.timestamp < c.ct AND m.timestamp >= c.ct - 600000
              ORDER BY (COALESCE(m.output_tokens,0) + COALESCE(m.cache_creation,0)) DESC
              LIMIT 500"""
    raw = _rows(c, sql, tuple(comp_params))
    # Dedupe to one row per (session_id, compact_ts) — already sorted by bloat desc, so first wins.
    seen: set[tuple] = set()
    out: list[dict] = []
    for r in raw:
        k = (r["session_id"], r["compact_ts"])
        if k in seen:
            continue
        seen.add(k)
        out.append(r)
        if len(out) >= 30:
            break
    return out


# ---------- composite insights -----------------------------------------

_INSIGHT_TOP_N = 15


def insights(filters: Filters | None = None) -> dict:
    """Composite bottleneck report.

    Orchestrates over every registered detector plus the entity-level
    reasoning/cache aggregations. Each section capped at _INSIGHT_TOP_N
    rows so the payload stays under MCP token limits. The set of
    sections grows automatically when new detectors register — agents
    don't need to know about plugin internals.
    """
    from .plugins import registry as _registry

    _registry.load_all()
    c = _conn()
    f = filters or {}

    def _cap(rows):
        return rows[:_INSIGHT_TOP_N] if isinstance(rows, list) else rows

    out: dict = {}
    # Run every registered detector with its default params.
    for name, det in _registry.detectors.items():
        try:
            rows = det.run(c, f, {})
        except Exception as e:
            out[name] = {"error": f"{type(e).__name__}: {e}"}
            continue
        out[name] = _cap(rows)

    # Entity-level aggregations not modelled as detectors.
    out.setdefault("cost_outliers", _cap(cost_outliers(filters)))
    out.setdefault("compaction_root", _cap(compaction_root(None, filters)))
    out["reasoning_cache_by_model"] = _cap(reasoning_cache("model", filters))
    out["reasoning_cache_inefficient_sessions"] = _cap(_inefficient_sessions(filters))

    # Headline summary heuristic.
    real_errs = 0
    rejects = 0
    biggest = None
    biggest_score = 0
    for name, rows in out.items():
        if not isinstance(rows, list):
            continue
        for r in rows:
            real_errs += int(r.get("real_errs") or r.get("real_errors") or 0)
            rejects += int(r.get("rejects") or 0)
            score = int(r.get("real_errs") or r.get("real_errors") or 0)
            if score > biggest_score:
                biggest_score = score
                biggest = {
                    "detector": name,
                    "row": {
                        k: v
                        for k, v in r.items()
                        if k
                        in (
                            "prev_tool",
                            "next_tool",
                            "session_id",
                            "file_path",
                            "bash_command",
                            "status_class_top",
                            "recommendation",
                        )
                    },
                }
    out["summary"] = {
        "real_errors": real_errs,
        "rejections": rejects,
        "biggest_concern": biggest,
        "top_recommendation": (biggest or {}).get("row", {}).get("recommendation"),
    }
    return out


def _inefficient_sessions(filters: Filters | None = None) -> list[dict]:
    """Sessions where (1 - cache_hit_ratio) * cost is largest — wasted-cost ranking."""
    c = _conn()
    msg_w_nt, msg_p_nt = _filters_no_tool(filters)
    sql = f"""SELECT session_id, project,
                SUM(input_tokens) inp, SUM(output_tokens) out,
                SUM(cache_read) cr, SUM(cache_creation) cc,
                SUM(thinking_tokens) think,
                ROUND(SUM(cost_usd),4) cost
              FROM messages{msg_w_nt}
              GROUP BY session_id
              HAVING SUM(input_tokens) + SUM(cache_read) > 0 AND SUM(cost_usd) > 0
              ORDER BY (1.0 - 1.0*SUM(cache_read)/NULLIF(SUM(input_tokens)+SUM(cache_read),0))
                       * SUM(cost_usd) DESC
              LIMIT ?"""
    raw = _rows(c, sql, msg_p_nt + (_LIMIT_CAP,))
    out = []
    for r in raw:
        inp = r["inp"]
        cr = r["cr"]
        ot = r["out"]
        cc = r["cc"]
        out.append(
            {
                "session_id": r["session_id"],
                "project": r["project"],
                "cost": r["cost"],
                "cache_hit_ratio": round(cr / (cr + inp), 4) if (cr + inp) else None,
                "cache_creation_pct": round(cc / (inp + cc), 4) if (inp + cc) else None,
                "thinking_pct_of_output": round(r["think"] / ot, 4) if ot else None,
                "input_tokens": inp,
                "output_tokens": ot,
                "cache_read": cr,
                "cache_creation": cc,
            }
        )
    return out


# ---------- DB schema as markdown (for MCP resource) -------------------


def schema_markdown() -> str:
    c = _conn()
    rows = c.execute("""
        SELECT name, sql FROM sqlite_master
        WHERE type='table' AND name NOT LIKE 'sqlite_%'
        ORDER BY name""").fetchall()
    parts = ["# tokenscope DB schema\n"]
    for r in rows:
        parts.append(f"## `{r['name']}`\n```sql\n{r['sql']}\n```\n")
    # column counts per table for quick reference
    parts.append("## Indexes\n")
    idx = c.execute("""
        SELECT name, tbl_name, sql FROM sqlite_master
        WHERE type='index' AND sql IS NOT NULL ORDER BY tbl_name, name""").fetchall()
    for r in idx:
        parts.append(f"- `{r['name']}` on `{r['tbl_name']}`: `{r['sql']}`")
    return "\n".join(parts)
