"""bash_retries — same bash_command repeated within window with ≥1 real error.

Reject-aware: excludes is_user_rejection=1.
"""
from __future__ import annotations

from ....analytics_core import _build_filters
from ...registry import registry


class BashRetries:
    name = "bash_retries"
    title = "Bash retry loops"
    description = (
        "Same bash_command run ≥2 times within window_s seconds in one "
        "session, with ≥1 real (non-reject) error. Surfaces wasted bash spend."
    )
    params_schema = {
        "type": "object",
        "properties": {
            "window_s": {"type": "integer", "minimum": 1, "maximum": 3600, "default": 60},
        },
    }
    requires = ("bash_meta", "status_class")

    def run(self, conn, filters, params):
        window_s = max(1, min(int(params.get("window_s", 60)), 3600))
        _, _, tc_w, tc_p = _build_filters(filters)
        extra = "AND tool_name='Bash' AND bash_command IS NOT NULL"
        where = (tc_w + " " + extra) if tc_w else " WHERE " + extra.lstrip("AND ").lstrip()
        sql = f"""
        WITH lagged AS (
          SELECT session_id, bash_command, timestamp, is_error, exit_code,
            COALESCE(is_user_rejection,0) is_user_rejection,
            status_class, result_text_snippet,
            LAG(bash_command) OVER (PARTITION BY session_id ORDER BY timestamp) prev_cmd,
            LAG(timestamp)    OVER (PARTITION BY session_id ORDER BY timestamp) prev_ts
          FROM tool_calls{where})
        SELECT session_id, bash_command, COUNT(*) retries,
          SUM(CASE WHEN is_error=1 AND is_user_rejection=0 THEN 1 ELSE 0 END) real_errors,
          SUM(is_user_rejection) rejects,
          MAX(exit_code) last_exit,
          (SELECT status_class FROM tool_calls
           WHERE session_id=lagged.session_id AND bash_command=lagged.bash_command
             AND is_error=1 AND COALESCE(is_user_rejection,0)=0
           GROUP BY status_class ORDER BY COUNT(*) DESC LIMIT 1) status_class_top,
          (SELECT result_text_snippet FROM tool_calls
           WHERE session_id=lagged.session_id AND bash_command=lagged.bash_command
             AND result_text_snippet IS NOT NULL LIMIT 1) result_sample
        FROM lagged
        WHERE bash_command = prev_cmd AND (timestamp - prev_ts) < ? * 1000
        GROUP BY session_id, bash_command
        HAVING retries >= 2 AND real_errors >= 1
        ORDER BY real_errors DESC, retries DESC LIMIT 50
        """
        rows = [dict(r) for r in conn.execute(sql, tc_p + (window_s,)).fetchall()]
        for r in rows:
            cls = r.get("status_class_top")
            if cls == "denied":
                r["recommendation"] = "Permission denied — adjust settings.json or use alternative tool"
            elif cls == "bash_exit_nonzero":
                r["recommendation"] = "Real exit-code failure — fix the underlying error before retrying"
            elif cls == "timeout":
                r["recommendation"] = "Command timed out repeatedly; raise timeout or split work"
            else:
                r["recommendation"] = "Repeated failing command — investigate root cause"
        return rows


registry.register_detector(BashRetries())
