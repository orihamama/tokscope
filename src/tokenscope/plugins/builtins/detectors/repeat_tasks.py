"""repeat_tasks — Agent invocations with the same fingerprint
(agent_subtype + normalized description prefix) repeated ≥N times.

Surfaces tasks the user keeps spawning manually that could be cached,
templated, or automated.
"""
from __future__ import annotations

import re

from ....analytics_core import _build_filters
from ...registry import registry


_NORM_RE = re.compile(r"\s+")


def _fingerprint(desc: str | None) -> str:
    if not desc:
        return ""
    s = _NORM_RE.sub(" ", desc.strip().lower())[:80]
    return s


class RepeatTasks:
    name = "repeat_tasks"
    title = "Repeated task templates"
    description = (
        "Agent (sub-task) invocations grouped by agent_subtype + normalized "
        "description prefix. Reveals tasks invoked ≥N times across sessions."
    )
    params_schema = {
        "type": "object",
        "properties": {
            "min_repeats": {"type": "integer", "minimum": 2, "default": 2},
        },
    }
    requires = ()

    def run(self, conn, filters, params):
        min_repeats = max(2, int(params.get("min_repeats", 2)))
        # tasks table has its own schema; we reuse the message-table filters
        # for cross-cutting since/until via session_id existence.
        msg_w, msg_p, _, _ = _build_filters(filters)
        if msg_w:
            sql = f"""
            SELECT t.agent_type, t.description, t.project, t.total_cost_usd cost,
                   t.session_id
            FROM tasks t
            WHERE t.session_id IN (SELECT DISTINCT session_id FROM messages{msg_w})
            """
            rows = conn.execute(sql, msg_p).fetchall()
        else:
            rows = conn.execute(
                "SELECT agent_type, description, project, total_cost_usd cost, session_id FROM tasks"
            ).fetchall()
        groups: dict[tuple, dict] = {}
        for r in rows:
            sig = _fingerprint(r["description"])
            if not sig:
                continue
            key = (r["agent_type"] or "?", sig)
            slot = groups.setdefault(key, {
                "agent_type": key[0],
                "description_sig": sig,
                "repeats": 0,
                "total_cost": 0.0,
                "projects": set(),
                "sessions": set(),
            })
            slot["repeats"] += 1
            slot["total_cost"] += r["cost"] or 0.0
            if r["project"]: slot["projects"].add(r["project"])
            if r["session_id"]: slot["sessions"].add(r["session_id"])
        out = []
        for slot in groups.values():
            if slot["repeats"] < min_repeats:
                continue
            cross = len(slot["projects"]) > 1
            slot["projects"] = sorted(slot["projects"])
            slot["sessions"] = sorted(slot["sessions"])[:10]
            slot["total_cost"] = round(slot["total_cost"], 4)
            slot["cross_project"] = cross
            slot["recommendation"] = (
                f"Task fingerprint repeated {slot['repeats']}× "
                f"({'across projects' if cross else 'in one project'}); "
                f"consider caching, scripting, or parameterising."
            )
            out.append(slot)
        out.sort(key=lambda r: (r["repeats"], r["total_cost"]), reverse=True)
        return out[:30]


registry.register_detector(RepeatTasks())
