"""Unit tests for built-in extractors."""
from __future__ import annotations

import pytest

from tokenscope.plugins import ExtractCtx, registry


@pytest.fixture(scope="module", autouse=True)
def _load() -> None:
    registry.load_all()


# ---------- bash_meta ----------

def test_bash_meta_extracts_program_and_subcommand():
    ex = registry.extractors["bash_meta"]
    ctx = ExtractCtx(session_id="S", project="p", source_file="f",
                     target="tool_call", tool_name="Bash",
                     tool_input={"command": "git log --oneline -20"})
    out = ex.extract({}, ctx)
    assert out["bash_program"] == "git"
    assert out["bash_subcommand"] == "log"
    assert out["bash_category"] == "vcs"


def test_bash_meta_skips_non_bash():
    ex = registry.extractors["bash_meta"]
    ctx = ExtractCtx(session_id="S", project="p", source_file="f",
                     target="tool_call", tool_name="Read", tool_input={})
    assert ex.extract({}, ctx) is None


# ---------- bash_touched_files ----------

def test_bash_touched_files_redirect_and_mv():
    ex = registry.extractors["bash_touched_files"]
    cases = [
        ("echo x > /tmp/out.txt", ["/tmp/out.txt"]),
        ("cat a >> b.log", ["b.log"]),
        ("mv old.py new.py", ["new.py"]),
        ("cp src.txt dst.txt", ["dst.txt"]),
        ("touch /tmp/marker", ["/tmp/marker"]),
        ("mkdir -p out/dir", ["out/dir"]),
        ("git restore foo.py", ["foo.py"]),
    ]
    for cmd, expected in cases:
        ctx = ExtractCtx(session_id="S", project="p", source_file="f",
                         target="tool_call", tool_name="Bash",
                         tool_input={"command": cmd})
        out = ex.extract({}, ctx) or {}
        import json
        files = json.loads(out.get("touched_files") or "[]")
        for e in expected:
            assert e in files, f"expected {e} in {files} for {cmd!r}"


def test_bash_touched_files_filters_dev_null():
    ex = registry.extractors["bash_touched_files"]
    ctx = ExtractCtx(session_id="S", project="p", source_file="f",
                     target="tool_call", tool_name="Bash",
                     tool_input={"command": "ls 2>/dev/null"})
    out = ex.extract({}, ctx)
    assert out is None  # /dev/null filtered → no useful targets → skip


# ---------- read_range ----------

def test_read_range_captures_offset_and_limit():
    ex = registry.extractors["read_range"]
    ctx = ExtractCtx(session_id="S", project="p", source_file="f",
                     target="tool_call", tool_name="Read",
                     tool_input={"offset": 100, "limit": 50})
    out = ex.extract({}, ctx)
    assert out == {"read_offset": 100, "read_limit": 50}


def test_read_range_returns_none_when_unset():
    ex = registry.extractors["read_range"]
    ctx = ExtractCtx(session_id="S", project="p", source_file="f",
                     target="tool_call", tool_name="Read",
                     tool_input={})
    assert ex.extract({}, ctx) is None


# ---------- status_class ----------

def test_status_class_classifies_user_rejection():
    from tokenscope.plugins.builtins.extractors.status_class import classify
    cls, rej = classify(
        "The user doesn't want to proceed with this tool use",
        "Bash", None, True, False)
    assert cls == "user_rejection"
    assert rej == 1


def test_status_class_distinguishes_denied_vs_other_errors():
    from tokenscope.plugins.builtins.extractors.status_class import classify
    cls, _ = classify("Permission to use Bash has been denied", "Bash", None, True, False)
    assert cls == "denied"
    cls, _ = classify("Cannot resume agent X: it is still running. Use TaskStop",
                       "Agent", None, True, False)
    assert cls == "agent_busy"
    cls, _ = classify("Request failed with status code 404", "WebFetch", None, True, False)
    assert cls == "http_error"
    cls, _ = classify("You are not in plan mode", "ExitPlanMode", None, True, False)
    assert cls == "not_in_plan_mode"


def test_status_class_success_path():
    from tokenscope.plugins.builtins.extractors.status_class import classify
    cls, _ = classify("ok", "Bash", 0, False, False)
    assert cls == "success"


def test_status_class_bash_exit_code():
    from tokenscope.plugins.builtins.extractors.status_class import classify
    cls, _ = classify("error", "Bash", 2, True, False)
    assert cls == "bash_exit_nonzero"
