"""Sample user-authored detector.

Drop this file at ~/.config/tokenscope/plugins/sample_detector.py to make
it auto-discoverable, or copy the pattern for your own detector.

Detects sessions running for more than `min_hours` (default 24h) — long
sessions accumulate context-rebuild overhead and lose attention focus.
"""

from __future__ import annotations

from tokenscope.analytics_core import _build_filters
from tokenscope.plugins import registry


class LongSessions:
    name = "long_sessions"
    title = "Sessions running >N hours"
    description = (
        "Sessions whose duration (ended_at - started_at) exceeds the "
        "threshold. Long sessions tend to compound context overhead."
    )
    params_schema = {
        "type": "object",
        "properties": {
            "min_hours": {"type": "number", "minimum": 1, "default": 24},
        },
    }
    requires = ()

    def run(self, conn, filters, params):
        min_hours = float(params.get("min_hours", 24))
        msg_w, msg_p, _, _ = _build_filters(filters)
        # Use messages-table filter to get session ids in the requested window.
        if msg_w:
            sql = f"""
            SELECT s.session_id, s.project,
                   ROUND((s.ended_at - s.started_at) / 3600000.0, 1) hours,
                   ROUND(s.total_cost_usd, 2) cost,
                   s.message_count messages,
                   s.tool_call_count tools
            FROM sessions s
            WHERE s.ended_at IS NOT NULL AND s.started_at IS NOT NULL
              AND (s.ended_at - s.started_at) >= ? * 3600000
              AND s.session_id IN (SELECT DISTINCT session_id FROM messages{msg_w})
            ORDER BY hours DESC LIMIT 30
            """
            rows = [dict(r) for r in conn.execute(sql, (min_hours, *msg_p))]
        else:
            sql = """
            SELECT session_id, project,
                   ROUND((ended_at - started_at) / 3600000.0, 1) hours,
                   ROUND(total_cost_usd, 2) cost,
                   message_count messages,
                   tool_call_count tools
            FROM sessions
            WHERE ended_at IS NOT NULL AND started_at IS NOT NULL
              AND (ended_at - started_at) >= ? * 3600000
            ORDER BY hours DESC LIMIT 30
            """
            rows = [dict(r) for r in conn.execute(sql, (min_hours,))]
        for r in rows:
            r["recommendation"] = (
                f"Session ran {r['hours']:.0f}h; consider splitting work into "
                f"fresh sessions to avoid context-rebuild overhead."
            )
        return rows


# Registration triggers when this module is imported by the registry.
registry.register_detector(LongSessions())
