"""redundant_read_ranges — per (session, file) line-interval coverage.

Compares total lines read against unique lines covered by merging
[read_offset, read_offset+result_lines] intervals. High redundancy_factor
flags genuinely wasted re-reads that cache cannot fully neutralise.
"""

from __future__ import annotations

from ....analytics_core import _build_filters
from ...registry import registry


def _merge_intervals(intervals: list[tuple[int, int]]) -> int:
    """Sort + merge [start,end] intervals; return total covered length."""
    if not intervals:
        return 0
    intervals = sorted(intervals)
    merged = [intervals[0]]
    for s, e in intervals[1:]:
        ms, me = merged[-1]
        if s <= me + 1:
            merged[-1] = (ms, max(me, e))
        else:
            merged.append((s, e))
    return sum(e - s + 1 for s, e in merged)


class RedundantReadRanges:
    name = "redundant_read_ranges"
    title = "Redundant read ranges (interval merge)"
    description = (
        "Per (session, file): merges line-ranges from Read offset/limit + "
        "result_lines. Flags files where total_lines_read / unique_lines "
        "ratio (redundancy_factor) ≥ threshold."
    )
    params_schema = {
        "type": "object",
        "properties": {
            "min_redundancy": {"type": "number", "minimum": 1.0, "default": 2.0},
            "min_reads": {"type": "integer", "minimum": 2, "default": 3},
        },
    }
    requires = ("read_range",)

    def run(self, conn, filters, params):
        min_redundancy = max(1.0, float(params.get("min_redundancy", 2.0)))
        min_reads = max(2, int(params.get("min_reads", 3)))
        _, _, tc_w, tc_p = _build_filters(filters)
        sql = f"""
        SELECT session_id, file_path, read_offset, read_limit, result_lines,
               attributed_cost_usd cost
        FROM tool_calls
        {tc_w + " AND " if tc_w else " WHERE "}
            tool_name='Read' AND file_path IS NOT NULL
        """
        per_pair: dict[tuple, dict] = {}
        for r in conn.execute(sql, tc_p):
            key = (r["session_id"], r["file_path"])
            slot = per_pair.setdefault(
                key,
                {
                    "intervals": [],
                    "reads": 0,
                    "total_lines": 0,
                    "cost": 0.0,
                    "with_offset": 0,
                },
            )
            slot["reads"] += 1
            slot["cost"] += r["cost"] or 0.0
            offset = r["read_offset"]
            limit = r["read_limit"]
            lines = r["result_lines"]
            if offset is not None and lines:
                slot["intervals"].append((offset, offset + max(int(lines), 1) - 1))
                slot["total_lines"] += int(lines)
                slot["with_offset"] += 1
            elif offset is not None and limit:
                slot["intervals"].append((offset, offset + max(int(limit), 1) - 1))
                slot["total_lines"] += int(limit)
                slot["with_offset"] += 1
            elif lines:
                # whole-file read — model as [1, lines] best-effort
                slot["intervals"].append((1, int(lines)))
                slot["total_lines"] += int(lines)
        out: list[dict] = []
        for (sid, fp), s in per_pair.items():
            if s["reads"] < min_reads or s["total_lines"] <= 0:
                continue
            unique = _merge_intervals(s["intervals"])
            if unique <= 0:
                continue
            ratio = s["total_lines"] / unique
            if ratio < min_redundancy:
                continue
            wasted = round(s["cost"] * (1 - 1 / ratio), 4)
            out.append(
                {
                    "session_id": sid,
                    "file_path": fp,
                    "reads": s["reads"],
                    "with_offset_calls": s["with_offset"],
                    "total_lines_read": s["total_lines"],
                    "unique_lines_covered": unique,
                    "redundancy_factor": round(ratio, 2),
                    "cost": round(s["cost"], 4),
                    "wasted_cost_estimate": wasted,
                    "recommendation": _rec(s["reads"], ratio, fp),
                }
            )
        out.sort(key=lambda r: (r["wasted_cost_estimate"], r["redundancy_factor"]), reverse=True)
        return out[:50]


def _rec(reads, ratio, fp):
    short = fp.rsplit("/", 1)[-1] if fp else "?"
    if ratio >= 5:
        return f"{short}: read {ratio:.1f}× more lines than file size; switch to Grep"
    if reads >= 10:
        return f"{short} re-read {reads}× with {ratio:.1f}× ratio; cache helps but Grep would be cheaper"
    return None


registry.register_detector(RedundantReadRanges())
