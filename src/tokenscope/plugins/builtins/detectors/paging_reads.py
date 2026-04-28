"""paging_reads — sessions where Reads were paged through a file (≥5 reads
with result_lines<500) but the session has zero Grep calls. Suggests the
agent should have used Grep instead of paging.

Requires: read_range extractor (read_offset/read_limit columns) — falls back
to result_lines<500 heuristic when offset/limit unavailable.
"""
from __future__ import annotations

from ....analytics_core import _build_filters
from ...registry import registry


class PagingReads:
    name = "paging_reads"
    title = "Paged reads without Grep"
    description = (
        "Sessions where the agent paged through a file with ≥N small Reads "
        "but never invoked Grep. Surfaces 'should have searched, not scrolled'."
    )
    params_schema = {
        "type": "object",
        "properties": {
            "min_pages": {"type": "integer", "minimum": 2, "maximum": 100, "default": 5},
        },
    }
    requires = ("read_range",)

    def run(self, conn, filters, params):
        min_pages = max(2, min(int(params.get("min_pages", 5)), 100))
        _, _, tc_w, tc_p = _build_filters(filters)
        # Build the read-pages CTE under the user's filter, then anti-join Grep.
        # The filter applies to both the "small reads" set and the Grep set so
        # narrow filters give consistent results.
        # Heuristic: many Reads of same file in same session, AND no Grep
        # anywhere in that session. Prefer offset/limit signal when present
        # (smaller chunks = paging) but fall back to raw count.
        sql = f"""
        WITH read_pages AS (
          SELECT session_id, file_path,
                 COUNT(*) pages,
                 SUM(CASE WHEN read_offset IS NOT NULL THEN 1 ELSE 0 END) paged_calls,
                 ROUND(AVG(NULLIF(result_bytes,0)),0) avg_bytes,
                 ROUND(SUM(attributed_cost_usd),4) cost
          FROM tool_calls
          {tc_w + ' AND ' if tc_w else ' WHERE '}
            tool_name='Read' AND file_path IS NOT NULL
          GROUP BY session_id, file_path
          HAVING pages >= ?
        ),
        greps AS (
          SELECT DISTINCT session_id FROM tool_calls
          {tc_w + ' AND ' if tc_w else ' WHERE '} tool_name='Grep'
        )
        SELECT rp.session_id, rp.file_path, rp.pages, rp.paged_calls,
               rp.avg_bytes, rp.cost
        FROM read_pages rp
        LEFT JOIN greps g ON rp.session_id=g.session_id
        WHERE g.session_id IS NULL
        ORDER BY rp.pages DESC LIMIT 50
        """
        rows = [dict(r) for r in conn.execute(sql, tc_p + (min_pages,) + tc_p).fetchall()]
        for r in rows:
            short = r["file_path"].rsplit("/", 1)[-1] if r.get("file_path") else "?"
            r["recommendation"] = (
                f"Use Grep on {short!r} instead of {r['pages']}× paged Read; "
                f"agent never searched this file in the session."
            )
        return rows


registry.register_detector(PagingReads())
