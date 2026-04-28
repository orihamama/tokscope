"""dead_search_patterns — Grep/Glob patterns called repeatedly with no
follow-up Read in the same session within window_s seconds. Surfaces
searches the agent ran but never acted on (wasted spend).
"""

from __future__ import annotations

from ....analytics_core import _build_filters
from ...registry import registry


class DeadSearchPatterns:
    name = "dead_search_patterns"
    title = "Search patterns with no Read follow-up"
    description = (
        "Grep / Glob / WebSearch patterns called ≥N times with zero Read "
        "or WebFetch within the next window_s seconds in the same session."
    )
    params_schema = {
        "type": "object",
        "properties": {
            "min_calls": {"type": "integer", "minimum": 1, "default": 3},
            "window_s": {"type": "integer", "minimum": 5, "default": 60},
        },
    }
    requires = ()

    def run(self, conn, filters, params):
        min_calls = max(1, int(params.get("min_calls", 3)))
        window_s = max(5, int(params.get("window_s", 60)))
        _, _, tc_w, tc_p = _build_filters(filters)
        sql = f"""
        WITH searches AS (
          SELECT id, session_id, search_pattern, web_query, tool_name, timestamp,
                 attributed_cost_usd cost
          FROM tool_calls
          {tc_w + " AND " if tc_w else " WHERE "}
            tool_name IN ('Grep','Glob','WebSearch')
        ),
        followups AS (
          SELECT s.id sid,
                 (SELECT COUNT(*) FROM tool_calls f
                  WHERE f.session_id=s.session_id
                    AND f.tool_name IN ('Read','WebFetch')
                    AND f.timestamp BETWEEN s.timestamp AND s.timestamp + ?*1000) f_count
          FROM searches s
        )
        SELECT s.tool_name,
               COALESCE(s.search_pattern, s.web_query) pattern,
               COUNT(*) calls,
               SUM(CASE WHEN f.f_count = 0 THEN 1 ELSE 0 END) dead_calls,
               ROUND(SUM(s.cost),4) cost,
               COUNT(DISTINCT s.session_id) sessions
        FROM searches s JOIN followups f ON s.id = f.sid
        WHERE COALESCE(s.search_pattern, s.web_query) IS NOT NULL
        GROUP BY s.tool_name, COALESCE(s.search_pattern, s.web_query)
        HAVING calls >= ? AND dead_calls = calls
        ORDER BY cost DESC LIMIT 50
        """
        rows = [dict(r) for r in conn.execute(sql, tc_p + (window_s, min_calls)).fetchall()]
        for r in rows:
            r["recommendation"] = (
                f"{r['tool_name']} pattern {r['pattern']!r} ran {r['calls']}× "
                f"with no Read/WebFetch follow-up — refine pattern or drop the call"
            )
        return rows


registry.register_detector(DeadSearchPatterns())
