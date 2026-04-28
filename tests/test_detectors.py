"""Unit tests for built-in detectors against the seeded synthetic DB."""

from __future__ import annotations

import pytest

from tokenscope.plugins import registry


@pytest.fixture(scope="module", autouse=True)
def _load():
    registry.load_all()


def test_permission_denials_finds_seeded_session(seeded_db):
    rows = registry.detectors["permission_denials"].run(seeded_db, {}, {"min_denials": 2})
    assert len(rows) == 1
    r = rows[0]
    assert r["session_id"] == "S1"
    assert r["denials"] == 3  # 3 seeded denied 'git status' calls
    assert "settings.json" in r["recommendation"]


def test_paging_reads_finds_no_grep_session(seeded_db):
    rows = registry.detectors["paging_reads"].run(seeded_db, {}, {"min_pages": 5})
    # S1 has 5 paged Reads of /repo/big.cpp and zero Grep calls.
    assert any(r["file_path"] == "/repo/big.cpp" and r["pages"] == 5 for r in rows)


def test_redundant_read_ranges_uses_interval_merge(seeded_db):
    # 5 reads of 200 lines starting at offsets 1, 201, 401, 601, 801 —
    # cover 1..1000 contiguously. Total lines read = 1000, unique = 1000,
    # redundancy = 1.0 → below default threshold (2.0) so no findings.
    rows = registry.detectors["redundant_read_ranges"].run(
        seeded_db, {}, {"min_redundancy": 1.0, "min_reads": 3}
    )
    # With min_redundancy=1.0 the set should include big.cpp.
    paths = {r["file_path"] for r in rows}
    assert "/repo/big.cpp" in paths


def test_agent_races_finds_busy_agent(seeded_db):
    rows = registry.detectors["agent_races"].run(seeded_db, {}, {})
    assert len(rows) == 1
    assert rows[0]["session_id"] == "S2"
    assert rows[0]["races"] == 1


def test_error_chains_excludes_user_rejections_by_default(seeded_db):
    # No user rejections in fixture, so all errors are "real".
    rows = registry.detectors["error_chains"].run(seeded_db, {}, {"min_n": 1, "min_rate": 0.0})
    # Bash→Bash chain (T1 → T2 both errors).
    assert any(r["prev_tool"] == "Bash" and r["next_tool"] == "Bash" for r in rows)


def test_bash_retries_filters_rejections(seeded_db):
    rows = registry.detectors["bash_retries"].run(seeded_db, {}, {"window_s": 60})
    # Two `git status` calls within ~200ms, both with status_class='denied'.
    assert any(r["bash_command"] == "git status" and r["retries"] >= 2 for r in rows)


def test_dead_search_patterns_empty_on_no_searches(seeded_db):
    # Fixture has no Grep/Glob/WebSearch calls.
    rows = registry.detectors["dead_search_patterns"].run(seeded_db, {}, {})
    assert rows == []


def test_repeat_tasks_no_repeats_in_fixture(seeded_db):
    rows = registry.detectors["repeat_tasks"].run(seeded_db, {}, {"min_repeats": 2})
    # Only one Agent task in fixture — no repeats.
    assert rows == []


def test_filters_scope_by_session(seeded_db):
    rows = registry.detectors["permission_denials"].run(seeded_db, {"session_id": "S2"}, {})
    # S2 has no denials.
    assert rows == []
    rows = registry.detectors["permission_denials"].run(
        seeded_db, {"session_id": "S1"}, {"min_denials": 2}
    )
    assert len(rows) == 1
