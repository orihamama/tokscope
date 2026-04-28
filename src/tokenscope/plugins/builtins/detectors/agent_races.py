"""agent_races — sessions where Agent invocations errored with status
'agent_busy', meaning the agent spawned a new task while a prior one was
still running. Indicates a concurrency bug in the workflow.
"""
from __future__ import annotations

from ....analytics_core import _build_filters
from ...registry import registry


class AgentRaces:
    name = "agent_races"
    title = "Concurrent Agent invocations"
    description = (
        "Sessions where Agent tool calls returned 'still running' errors. "
        "The agent attempted to spawn a new task while a prior subagent "
        "was still running. Wait for the prior task or call TaskStop first."
    )
    params_schema = {"type": "object", "properties": {}}
    requires = ("status_class",)

    def run(self, conn, filters, params):
        _, _, tc_w, tc_p = _build_filters(filters)
        sql = f"""
        SELECT session_id, project,
               COUNT(*) races,
               GROUP_CONCAT(DISTINCT SUBSTR(result_text_snippet,1,80)) samples
        FROM tool_calls
        {tc_w + ' AND ' if tc_w else ' WHERE '} status_class='agent_busy'
        GROUP BY session_id, project ORDER BY races DESC LIMIT 30
        """
        rows = [dict(r) for r in conn.execute(sql, tc_p).fetchall()]
        for r in rows:
            r["recommendation"] = (
                "Wait for prior Agent task to complete before spawning a new "
                "one, or call TaskStop first."
            )
        return rows


registry.register_detector(AgentRaces())
