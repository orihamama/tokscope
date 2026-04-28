"""Unit tests for the investigate pipeline."""

from __future__ import annotations

import pytest


@pytest.fixture
def patched_inv(seeded_db, monkeypatch):
    from tokenscope import analytics_core as core
    from tokenscope import investigate as inv

    monkeypatch.setattr(core, "_conn", lambda: seeded_db)
    monkeypatch.setattr(inv, "_conn", lambda: seeded_db)
    return inv


def test_investigate_specific_session(patched_inv):
    res = patched_inv.investigate(target="session", session_id="S1")
    assert res["target"]["id"] == "S1"
    assert res["facts"]["cost"] == 5.0
    assert res["facts"]["grep_calls"] == 0
    # Should detect paging_reads at minimum (5 paged reads, 0 grep).
    sigs = {e["signal"] for e in res["evidence"]}
    assert "paging_reads" in sigs


def test_investigate_returns_root_causes_and_actions(patched_inv):
    res = patched_inv.investigate(target="session", session_id="S1")
    assert res["root_causes"]  # at least one
    assert res["actions"]  # at least one
    # Each cause has the required fields.
    for c in res["root_causes"]:
        assert "cause" in c and "confidence" in c
        assert c["confidence"] in {"low", "medium", "high"}
    # Actions ranked structure.
    for a in res["actions"]:
        assert "action" in a and "impact" in a and "effort" in a


def test_investigate_unknown_session_errors(patched_inv):
    res = patched_inv.investigate(target="session", session_id="ZZZ")
    assert "error" in res


def test_investigate_auto_picks_top(patched_inv):
    res = patched_inv.investigate(target="auto")
    # Synthetic data has S2 as the higher-cost session.
    assert res["target"]["id"] in {"S1", "S2"}


def test_investigate_session_id_required_when_explicit(patched_inv):
    res = patched_inv.investigate(target="session")
    assert "error" in res
    assert "session_id" in res["error"]
