"""Deep investigation pipeline.

Given a target (session_id, or auto top-concern), gather facts, run all
detectors scoped to the target, score evidence, and synthesize:
  - summary (what happened, key issue, estimated impact)
  - evidence (ranked signals)
  - root_causes (pattern-matched explanations with confidence)
  - actions (ranked by impact × effort)
  - timeline (top events chronologically)

Keeps logic out of MCP layer; MCP `investigate` tool just calls in.
"""
from __future__ import annotations

import sqlite3
from typing import Any

from .analytics_core import (
    _LIMIT_CAP,
    _build_filters,
    _conn,
    cost_outliers,
    reasoning_cache,
)


# ---------- root cause matchers ---------------------------------------

def _match_root_causes(evidence: list[dict], facts: dict) -> list[dict]:
    """Pattern-match evidence sets into named root causes with confidence."""
    causes: list[dict] = []
    by_sig = {e["signal"]: e for e in evidence}
    fac = facts

    # paging-without-Grep — fires when EITHER detector finds a hit AND
    # session has zero Grep calls (the strong signal).
    rrr = by_sig.get("redundant_read_ranges")
    pr = by_sig.get("paging_reads")
    if (rrr or pr) and fac.get("grep_calls", 0) == 0:
        impact = float((rrr or {}).get("value_usd", 0)) + float((pr or {}).get("value_usd", 0))
        causes.append({
            "cause": "Agent paged through file with offset Reads instead of Grep",
            "confidence": "high",
            "evidence_signals": [s for s in ("redundant_read_ranges", "paging_reads")
                                 if s in by_sig],
            "estimated_impact_usd": round(impact, 4),
        })
    elif rrr and rrr.get("value_usd", 0) > 0.5:
        causes.append({
            "cause": "File re-read many times despite available cache; could use Grep for targeted lookup",
            "confidence": "medium",
            "evidence_signals": ["redundant_read_ranges"],
            "estimated_impact_usd": rrr.get("value_usd", 0.0),
        })

    # permission denials loop
    pd = by_sig.get("permission_denials")
    if pd and pd.get("count", 0) >= 3:
        causes.append({
            "cause": "Bash permission denials caused agent to retry blocked actions",
            "confidence": "high" if pd["count"] >= 10 else "medium",
            "evidence_signals": ["permission_denials"],
            "estimated_impact_usd": 0.0,
        })

    # agent concurrency races
    ar = by_sig.get("agent_races")
    if ar and ar.get("count", 0) > 0:
        causes.append({
            "cause": "Agent spawned new sub-task while prior was still running",
            "confidence": "high",
            "evidence_signals": ["agent_races"],
            "estimated_impact_usd": 0.0,
        })

    # bash build/test failure loop
    br = by_sig.get("bash_retries")
    if br and br.get("count", 0) >= 2:
        causes.append({
            "cause": f"Repeated bash command failed {br['count']}× — build/test broken or env mismatch",
            "confidence": "medium",
            "evidence_signals": ["bash_retries"],
            "estimated_impact_usd": br.get("value_usd", 0.0),
        })

    # http error cascade
    ec = by_sig.get("error_chains")
    if ec and ec.get("status_class") == "http_error":
        causes.append({
            "cause": "WebFetch URLs returning 4xx/5xx — invalid URLs or timing race",
            "confidence": "medium",
            "evidence_signals": ["error_chains"],
            "estimated_impact_usd": ec.get("value_usd", 0.0),
        })

    # context bloat / compaction risk
    if fac.get("max_input_tokens", 0) > 150000:
        causes.append({
            "cause": f"Single turn reached {fac['max_input_tokens']:,} input tokens — context near limit",
            "confidence": "high" if fac.get("compactions", 0) > 0 else "medium",
            "evidence_signals": ["context_pressure"],
            "estimated_impact_usd": 0.0,
        })

    # long-running session
    if fac.get("duration_hours", 0) > 24:
        causes.append({
            "cause": f"Session ran {fac['duration_hours']:.0f} hours; long sessions accumulate context overhead and re-discovery cost",
            "confidence": "medium",
            "evidence_signals": ["session_duration"],
            "estimated_impact_usd": 0.0,
        })

    # tool concentration
    if fac.get("top_tool_pct", 0) > 0.5:
        causes.append({
            "cause": f"{fac.get('top_tool_name','?')} dominates spend ({fac['top_tool_pct']*100:.0f}% of session cost)",
            "confidence": "low",
            "evidence_signals": ["tool_breakdown"],
            "estimated_impact_usd": 0.0,
        })

    return causes


def _suggest_actions(causes: list[dict], facts: dict) -> list[dict]:
    """Rank concrete actions by impact × effort."""
    actions: list[dict] = []
    seen: set[str] = set()
    def _add(label, action, impact, effort):
        if action in seen:
            return
        seen.add(action)
        actions.append({"label": label, "action": action,
                        "impact": impact, "effort": effort})

    for c in causes:
        cause = c["cause"]
        if "paged through file" in cause or "re-read many times" in cause:
            _add("Use Grep for targeted lookup",
                 "Prefer Grep over offset Read for files >2000 lines; fetch specific symbols, not pages.",
                 "high", "behavioral")
        if "permission denials" in cause:
            _add("Adjust permissions",
                 "Add the failing Bash command to settings.json `allow` list, or "
                 "switch the agent to a permitted alternative tool.",
                 "high", "config")
        if "still running" in cause or "concurrency" in cause:
            _add("Serialise Agent calls",
                 "Wait for the prior Agent to complete before spawning the next; "
                 "or call TaskStop first.",
                 "medium", "behavioral")
        if "bash command failed" in cause:
            _add("Fix root cause of bash failure",
                 "Inspect stderr/exit code of the repeated command and fix the "
                 "underlying issue rather than retrying.",
                 "high", "code")
        if "WebFetch" in cause or "http_error" in cause:
            _add("Validate URLs before WebFetch",
                 "Pre-check URLs (curl -I) or add retry-with-backoff before WebFetch.",
                 "medium", "behavioral")
        if "context near limit" in cause:
            _add("Split context",
                 "Run /compact before context fills; split work across sessions; "
                 "avoid Reading huge files into context.",
                 "high", "behavioral")
        if "Session ran" in cause and "hours" in cause:
            _add("Start fresh sessions",
                 "Open a new session per task; long sessions accumulate cache "
                 "rebuild overhead and lose attention focus.",
                 "medium", "behavioral")
    return actions


# ---------- evidence collector ---------------------------------------

def _gather_evidence(conn: sqlite3.Connection, filters: dict, facts: dict) -> list[dict]:
    """Run every registered detector scoped to filters; reduce each to one
    normalised evidence row."""
    from .plugins import registry as _registry
    _registry.load_all()
    evidence: list[dict] = []
    for name, det in _registry.detectors.items():
        try:
            rows = det.run(conn, filters, {})
        except Exception:
            continue
        if not rows:
            continue
        # Reduce to one summary row per detector.
        ev = _reduce_detector(name, rows)
        if ev:
            evidence.append(ev)
    # Sort by value_usd descending, ties by count
    evidence.sort(key=lambda e: (e.get("value_usd", 0), e.get("count", 0)), reverse=True)
    return evidence


def _reduce_detector(name: str, rows: list[dict]) -> dict | None:
    """Collapse N rows into one evidence summary."""
    if not rows:
        return None
    n = len(rows)
    val = 0.0
    sample = None
    for r in rows[:5]:
        for k in ("wasted_cost_estimate", "cost", "total_cost"):
            v = r.get(k)
            if isinstance(v, (int, float)):
                val += float(v); break
        if sample is None:
            sample = {k: v for k, v in r.items() if k in (
                "session_id", "file_path", "bash_command", "prev_tool",
                "next_tool", "status_class_top", "redundancy_factor",
                "pages", "denials", "races", "real_errs", "real_errors",
                "retries", "repeats", "recommendation"
            )}
    return {
        "signal": name,
        "count": n,
        "value_usd": round(val, 4),
        "top": sample,
    }


# ---------- session facts -------------------------------------------

def _session_facts(conn: sqlite3.Connection, session_id: str) -> dict:
    sess = conn.execute(
        "SELECT * FROM sessions WHERE session_id=?", (session_id,)
    ).fetchone()
    if not sess:
        return {}
    duration_hours = 0.0
    if sess["started_at"] and sess["ended_at"]:
        duration_hours = (sess["ended_at"] - sess["started_at"]) / 3600000

    tool_rows = [dict(r) for r in conn.execute(
        """SELECT tool_name, COUNT(*) calls, ROUND(SUM(attributed_cost_usd),4) cost
           FROM tool_calls WHERE session_id=?
           GROUP BY tool_name ORDER BY cost DESC""", (session_id,))]
    total_cost = sum(t["cost"] or 0 for t in tool_rows)
    top_tool = tool_rows[0] if tool_rows else None

    grep_calls = sum(t["calls"] for t in tool_rows if t["tool_name"] == "Grep")

    max_in_row = conn.execute(
        """SELECT MAX(input_tokens + cache_read) m FROM messages
           WHERE session_id=? AND role='assistant'""", (session_id,)
    ).fetchone()
    max_input = max_in_row["m"] if max_in_row else 0

    return {
        "session_id": session_id,
        "project": sess["project"],
        "cost": round(sess["total_cost_usd"] or 0, 4),
        "msgs": sess["message_count"],
        "tool_calls": sess["tool_call_count"],
        "errors": sess["error_count"],
        "compactions": sess["compaction_count"],
        "duration_hours": round(duration_hours, 1),
        "cache_hit_ratio": sess["cache_hit_ratio"],
        "max_input_tokens": max_input or 0,
        "tool_breakdown": tool_rows[:5],
        "top_tool_name": top_tool["tool_name"] if top_tool else None,
        "top_tool_pct": (top_tool["cost"] / total_cost) if (top_tool and total_cost) else 0,
        "grep_calls": grep_calls,
    }


def _session_timeline(conn: sqlite3.Connection, session_id: str, limit: int = 8) -> list[dict]:
    """Top expensive turns + first error of each kind."""
    rows = [dict(r) for r in conn.execute(
        """SELECT timestamp, model, output_tokens, cache_creation, cost_usd,
                  is_compact_summary
           FROM messages WHERE session_id=? AND role='assistant' AND cost_usd > 0
           ORDER BY cost_usd DESC LIMIT ?""", (session_id, limit))]
    return rows


# ---------- entry point ----------------------------------------------

def investigate(session_id: str | None = None,
                target: str = "auto",
                filters: dict | None = None) -> dict:
    """Run a deep investigation.

    Modes:
      target='session'     — inspect a specific session (session_id required)
      target='auto'        — pick the highest-cost session in `filters` window
      target='top_concern' — find the worst current bottleneck globally
    """
    conn = _conn()
    f = filters or {}

    # Resolve target session.
    if target == "session" and not session_id:
        return {"error": "session_id required for target='session'"}
    if target in ("auto", "top_concern") and not session_id:
        outliers = cost_outliers(f, z_min=2.0)
        if outliers:
            session_id = outliers[0]["session_id"]
        else:
            row = conn.execute(
                "SELECT session_id FROM sessions ORDER BY total_cost_usd DESC LIMIT 1"
            ).fetchone()
            session_id = row["session_id"] if row else None
    if not session_id:
        return {"error": "no session to investigate"}

    facts = _session_facts(conn, session_id)
    if not facts:
        return {"error": f"session not found: {session_id}"}

    scoped = {**f, "session_id": session_id}
    evidence = _gather_evidence(conn, scoped, facts)
    causes = _match_root_causes(evidence, facts)
    actions = _suggest_actions(causes, facts)
    timeline = _session_timeline(conn, session_id)

    # Reasoning + cache snapshot for this session.
    rc = reasoning_cache("session", scoped)
    rc_self = next((r for r in rc if r["key"] == session_id), None)

    estimated_waste = round(sum(c.get("estimated_impact_usd") or 0 for c in causes), 4)
    summary = {
        "what": (
            f"Session {session_id[:8]} ran {facts['duration_hours']:.0f} h, "
            f"${facts['cost']:.2f} cost, {facts['msgs']:,} messages, "
            f"{facts['tool_calls']:,} tool calls."
        ),
        "key_issue": (
            causes[0]["cause"] if causes else "no major root cause matched"
        ),
        "estimated_avoidable_usd": estimated_waste,
    }

    return {
        "target": {"type": "session", "id": session_id, "project": facts["project"]},
        "summary": summary,
        "facts": facts,
        "reasoning_cache": rc_self,
        "evidence": evidence,
        "root_causes": causes,
        "actions": actions,
        "top_expensive_turns": timeline,
    }
