"""permission_denials — sessions hitting status_class='denied' repeatedly.

Surfaces agents looping on bash permissions the user has denied.
"""

from __future__ import annotations

from ....analytics_core import _build_filters
from ...registry import registry


class PermissionDenials:
    name = "permission_denials"
    title = "Permission denial loops"
    description = (
        "Sessions where Bash (or other) tool calls repeatedly hit "
        "permission denials. Indicates the agent retries denied actions "
        "instead of pivoting; permission settings need adjustment or the "
        "agent prompt should recognise permanent denial."
    )
    params_schema = {
        "type": "object",
        "properties": {
            "min_denials": {"type": "integer", "minimum": 1, "maximum": 100, "default": 2},
        },
    }
    requires = ("status_class",)

    def run(self, conn, filters, params):
        min_denials = max(1, min(int(params.get("min_denials", 2)), 100))
        _, _, tc_w, tc_p = _build_filters(filters)
        sql = f"""
        SELECT session_id, project,
               COUNT(*) denials,
               COUNT(DISTINCT bash_command) distinct_cmds,
               GROUP_CONCAT(DISTINCT SUBSTR(bash_command,1,40)) sample_cmds,
               GROUP_CONCAT(DISTINCT SUBSTR(result_text_snippet,1,80)) sample_results
        FROM tool_calls
        {tc_w + " AND " if tc_w else " WHERE "} status_class='denied'
        GROUP BY session_id, project
        HAVING denials >= ?
        ORDER BY denials DESC LIMIT 30
        """
        rows = [dict(r) for r in conn.execute(sql, tc_p + (min_denials,)).fetchall()]
        for r in rows:
            r["recommendation"] = (
                f"Add Bash permission to settings.json or prompt the agent "
                f"to recognise denial; {r['denials']} retries observed."
            )
        return rows


registry.register_detector(PermissionDenials())
