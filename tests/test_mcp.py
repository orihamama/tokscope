"""Smoke tests for the MCP server: tool listing + happy-path call."""

from __future__ import annotations

import json

import pytest


@pytest.fixture
def patched_mcp(seeded_db, monkeypatch):
    """Point all MCP tool implementations at the seeded DB."""
    from tokenscope import analytics_core as core
    from tokenscope import investigate as inv

    monkeypatch.setattr(core, "_conn", lambda: seeded_db)
    monkeypatch.setattr(inv, "_conn", lambda: seeded_db)
    from tokenscope import mcp_server

    return mcp_server


@pytest.mark.asyncio
async def test_tools_list(patched_mcp):
    tools = await patched_mcp.list_tools()
    names = {t.name for t in tools}
    expected = {
        "get_overview",
        "get_insights",
        "get_top_costs",
        "get_session_detail",
        "get_reasoning_cache",
        "find_duplicate_reads",
        "find_bash_retries",
        "find_error_chains",
        "find_compaction_root",
        "investigate",
    }
    assert expected.issubset(names)


@pytest.mark.asyncio
async def test_resources_list(patched_mcp):
    res = await patched_mcp.list_resources()
    uris = {str(r.uri) for r in res}
    assert "analytics://schema" in uris


@pytest.mark.asyncio
async def test_call_get_overview(patched_mcp):
    out = await patched_mcp.call_tool("get_overview", {})
    parsed = json.loads(out[0].text)
    assert "spend" in parsed
    assert parsed["spend"]["all_time"] >= 0


@pytest.mark.asyncio
async def test_call_investigate(patched_mcp):
    out = await patched_mcp.call_tool("investigate", {"session_id": "S1", "target": "session"})
    parsed = json.loads(out[0].text)
    assert parsed["target"]["id"] == "S1"
    assert "root_causes" in parsed and "actions" in parsed


@pytest.mark.asyncio
async def test_call_unknown_tool_returns_error(patched_mcp):
    out = await patched_mcp.call_tool("not_a_tool", {})
    parsed = json.loads(out[0].text)
    assert "error" in parsed
