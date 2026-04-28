"""Unit tests for entity-level analytics_core functions."""

from __future__ import annotations


def test_overview_returns_expected_shape(patched_core):
    o = patched_core.overview({})
    assert "spend" in o
    assert "all_time" in o["spend"]
    assert "delta_today_pct" in o["spend"]
    assert "cache_hit_ratio" in o
    assert "top_projects" in o
    assert "top_tools" in o


def test_top_costs_by_tool(patched_core):
    rows = patched_core.top_costs("tool", limit=10)
    keys = {r["key"] for r in rows}
    assert "Bash" in keys
    assert "Read" in keys


def test_top_costs_by_session(patched_core):
    rows = patched_core.top_costs("session", limit=10)
    sessions = {r["key"] for r in rows}
    assert "S1" in sessions and "S2" in sessions


def test_top_costs_invalid_dimension(patched_core):
    rows = patched_core.top_costs("garbage", limit=5)
    assert rows and "error" in rows[0]


def test_top_costs_limit_clamped(patched_core):
    rows = patched_core.top_costs("tool", limit=100000)
    # Cap is 100; with synthetic data we expect <=100 anyway.
    assert len(rows) <= 100


def test_session_detail_summary(patched_core):
    d = patched_core.session_detail("S1")
    assert d["session"]["session_id"] == "S1"
    assert any(t["tool_name"] == "Bash" for t in d["tool_breakdown"])
    assert d["reasoning_cache"]["cache_hit_ratio"] is not None


def test_session_detail_unknown_returns_error(patched_core):
    d = patched_core.session_detail("DOES_NOT_EXIST")
    assert "error" in d


def test_reasoning_cache_grouping(patched_core):
    rows = patched_core.reasoning_cache("model", {})
    assert rows
    assert "cache_hit_ratio" in rows[0]


def test_insights_includes_summary_and_detector_sections(patched_core):
    ins = patched_core.insights({})
    assert "summary" in ins
    # Summary picks the highest real_errs detector row.
    summary = ins["summary"]
    assert "biggest_concern" in summary
    # All registered detectors should be represented.
    for name in (
        "paging_reads",
        "permission_denials",
        "agent_races",
        "duplicate_reads",
        "bash_retries",
        "error_chains",
        "redundant_read_ranges",
        "repeat_tasks",
        "dead_search_patterns",
    ):
        assert name in ins, f"missing detector section: {name}"
