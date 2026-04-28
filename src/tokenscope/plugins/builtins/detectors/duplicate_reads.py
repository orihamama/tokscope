"""duplicate_reads — same file_path Read ≥N times in a session with no
intervening Edit/Write. Cache makes it cheap, but pattern hints agent
should keep state in context.
"""

from __future__ import annotations

from ....analytics_core import _build_filters
from ...registry import registry


class DuplicateReads:
    name = "duplicate_reads"
    title = "Duplicate reads (no intervening edit)"
    description = (
        "Same file_path Read repeatedly in a session with no Edit/Write "
        "between. Surfaces 'agent re-reads instead of remembering'."
    )
    params_schema = {
        "type": "object",
        "properties": {
            "min_dups": {"type": "integer", "minimum": 1, "maximum": 50, "default": 2},
        },
    }
    requires = ()

    def run(self, conn, filters, params):
        min_dups = max(1, min(int(params.get("min_dups", 2)), 50))
        _, _, tc_w, tc_p = _build_filters(filters)
        extra = "AND file_path IS NOT NULL AND tool_name IN ('Read','Edit','Write','MultiEdit')"
        where = (tc_w + " " + extra) if tc_w else " WHERE " + extra.lstrip("AND ").lstrip()
        sql = f"""
        WITH ord AS (
          SELECT session_id, file_path, tool_name, timestamp,
            SUM(CASE WHEN tool_name IN ('Edit','Write','MultiEdit') THEN 1 ELSE 0 END)
              OVER (PARTITION BY session_id, file_path ORDER BY timestamp
                    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) AS edits_so_far
          FROM tool_calls{where})
        SELECT session_id, file_path,
          SUM(CASE WHEN tool_name='Read' THEN 1 ELSE 0 END) reads,
          COUNT(DISTINCT edits_so_far) edit_groups,
          SUM(CASE WHEN tool_name='Read' THEN 1 ELSE 0 END)
            - COUNT(DISTINCT edits_so_far) AS dup_reads
        FROM ord GROUP BY session_id, file_path
        HAVING dup_reads >= ?
        ORDER BY dup_reads DESC LIMIT 100
        """
        rows = [dict(r) for r in conn.execute(sql, tc_p + (min_dups,)).fetchall()]
        for r in rows:
            if r["dup_reads"] > 20:
                short = r["file_path"].rsplit("/", 1)[-1] if r.get("file_path") else "?"
                r["recommendation"] = f"Use Grep on {short!r} instead of {r['dup_reads']}× re-read"
            else:
                r["recommendation"] = None
        return rows


registry.register_detector(DuplicateReads())
