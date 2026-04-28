"""error_chains — tool bigrams whose next-tool errors disproportionately.

Reject-aware: filters is_user_rejection=0 by default so user-deliberate
plan rejections don't pollute findings. Pass include_rejections=true to
opt back in.
"""
from __future__ import annotations

from ....analytics_core import _build_filters
from ...registry import registry


class ErrorChains:
    name = "error_chains"
    title = "Tool sequences that consistently fail"
    description = (
        "Bigrams (prev_tool → next_tool) where next_tool errors at rate "
        "≥ min_rate over ≥ min_n samples. Excludes user_rejection by default."
    )
    params_schema = {
        "type": "object",
        "properties": {
            "min_n": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 5},
            "min_rate": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.2},
            "include_rejections": {"type": "boolean", "default": False},
        },
    }
    requires = ("status_class",)

    def run(self, conn, filters, params):
        min_n = max(1, min(int(params.get("min_n", 5)), 1000))
        min_rate = max(0.0, min(float(params.get("min_rate", 0.2)), 1.0))
        include_rej = bool(params.get("include_rejections", False))
        _, _, tc_w, tc_p = _build_filters(filters)
        # When excluding rejections, define "real error" as is_error=1 AND
        # is_user_rejection=0. Else is_error=1.
        err_pred = "is_error=1" if include_rej else (
            "is_error=1 AND COALESCE(is_user_rejection,0)=0"
        )
        sql = f"""
        WITH lagged AS (
          SELECT tool_name AS next_tool, is_error,
                 COALESCE(is_user_rejection,0) is_user_rejection,
                 status_class, result_text_snippet, session_id,
                 LAG(tool_name) OVER (PARTITION BY session_id
                                      ORDER BY timestamp, id) prev_tool
          FROM tool_calls{tc_w})
        SELECT prev_tool, next_tool, COUNT(*) n,
          SUM(CASE WHEN {err_pred} THEN 1 ELSE 0 END) real_errs,
          SUM(is_user_rejection) rejects,
          ROUND(1.0*SUM(CASE WHEN {err_pred} THEN 1 ELSE 0 END)/COUNT(*),3) err_rate,
          (SELECT status_class FROM tool_calls
           WHERE tool_name=lagged.next_tool AND is_error=1
             AND COALESCE(is_user_rejection,0)=0
           GROUP BY status_class ORDER BY COUNT(*) DESC LIMIT 1) status_class_top,
          (SELECT result_text_snippet FROM tool_calls
           WHERE tool_name=lagged.next_tool AND is_error=1
             AND COALESCE(is_user_rejection,0)=0
             AND result_text_snippet IS NOT NULL
           LIMIT 1) result_sample
        FROM lagged
        WHERE prev_tool IS NOT NULL
        GROUP BY prev_tool, next_tool
        HAVING n >= ? AND err_rate >= ?
        ORDER BY real_errs DESC LIMIT 30
        """
        rows = [dict(r) for r in conn.execute(sql, tc_p + (min_n, min_rate)).fetchall()]
        for r in rows:
            r["recommendation"] = _rec(r)
        return rows


def _rec(row):
    cls = row.get("status_class_top")
    nxt = row.get("next_tool")
    if cls == "not_in_plan_mode":
        return "Agent calls ExitPlanMode outside plan mode; suppress unless permissionMode='plan'"
    if cls == "http_error":
        return f"Validate URL or add retry-with-backoff before {nxt}"
    if cls == "agent_busy":
        return "Wait for prior Agent to complete before spawning new one (or call TaskStop first)"
    if cls == "denied":
        return "Add permission to settings.json or use a safer alternative"
    if cls == "timeout":
        return f"{nxt} timed out repeatedly; raise timeout or split work"
    if cls == "bash_exit_nonzero":
        return f"Bash exit code patterns; inspect command and target"
    return None


registry.register_detector(ErrorChains())
