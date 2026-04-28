"""Plugin registry smoke tests."""
from __future__ import annotations

import sqlite3

import pytest

from tokenscope.plugins import Detector, Extractor, registry


@pytest.fixture(scope="module", autouse=True)
def _load() -> None:
    registry.load_all()


def test_builtin_extractors_loaded() -> None:
    expected = {"bash_meta", "bash_touched_files", "read_range", "status_class"}
    assert expected.issubset(set(registry.extractors))


def test_builtin_detectors_loaded() -> None:
    expected = {
        "agent_races", "bash_retries", "dead_search_patterns",
        "duplicate_reads", "error_chains", "paging_reads",
        "permission_denials", "redundant_read_ranges", "repeat_tasks",
    }
    assert expected.issubset(set(registry.detectors))


def test_extractors_satisfy_protocol() -> None:
    for name, ex in registry.extractors.items():
        assert isinstance(ex, Extractor), f"{name} doesn't satisfy Extractor"
        fields = ex.fields()
        assert isinstance(fields, dict)
        for col, typ in fields.items():
            assert isinstance(col, str) and isinstance(typ, str)
            assert typ.upper() in {"INTEGER", "TEXT", "REAL"}


def test_detectors_satisfy_protocol() -> None:
    for name, det in registry.detectors.items():
        assert isinstance(det, Detector), f"{name} doesn't satisfy Detector"
        assert det.title and det.description
        assert isinstance(det.params_schema, dict)


def test_status_class_classifies() -> None:
    """Unit test the status_class extractor's classifier directly."""
    from tokenscope.plugins.builtins.extractors.status_class import classify

    cls, rej = classify("The user doesn't want to proceed", "Bash", None, True, False)
    assert cls == "user_rejection" and rej == 1

    cls, rej = classify("Permission to use Bash has been denied", "Bash", None, True, False)
    assert cls == "denied" and rej == 0

    cls, _ = classify("You are not in plan mode", "ExitPlanMode", None, True, False)
    assert cls == "not_in_plan_mode"

    cls, _ = classify("Cannot resume agent X: it is still running. Use TaskStop", "Agent", None, True, False)
    assert cls == "agent_busy"

    cls, _ = classify("Request failed with status code 404", "WebFetch", None, True, False)
    assert cls == "http_error"

    cls, _ = classify("ok", "Bash", 0, False, False)
    assert cls == "success"


def test_paging_reads_runs_on_empty_db(tmp_path) -> None:
    """Detector should not crash on an empty DB."""
    db = tmp_path / "t.db"
    conn = sqlite3.connect(db)
    from tokenscope.db import init_schema
    init_schema(conn)
    rows = registry.detectors["paging_reads"].run(conn, {}, {})
    assert rows == []
