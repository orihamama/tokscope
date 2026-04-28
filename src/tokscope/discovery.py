"""Scan ~/.claude/projects/ for JSONL session files."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .paths import PROJECTS_DIR


@dataclass
class JsonlFile:
    path: Path
    project: str
    session_id: str
    is_subagent: bool
    mtime: float
    size: int


# Subagent ephemeral worktrees: skip entirely. Parent session captures all
# Agent tool invocations + subagent JSONL linkage via the agent_id join.
_EPHEMERAL_DIR_RE = re.compile(r"^-private-tmp-agent-")
# Git worktrees Claude Code spawns under <project>/.claude/worktrees/<name>.
# Roll their analytics back up into the parent project.
_WORKTREE_RE = re.compile(r"^(.*?)-+claude-worktrees-")


def _decode_project(dirname: str) -> str:
    """Project dir name is original cwd with `/` → `-`. Best-effort restore.
    Worktree subdirs are normalized back to their parent project."""
    m = _WORKTREE_RE.match(dirname)
    if m:
        dirname = m.group(1)
    s = dirname.lstrip("-")
    return "/" + s.replace("-", "/")


def is_ephemeral_project_dir(dirname: str) -> bool:
    return bool(_EPHEMERAL_DIR_RE.match(dirname))


def discover(root: Path | None = None) -> list[JsonlFile]:
    root = root or PROJECTS_DIR
    if not root.exists():
        return []
    out: list[JsonlFile] = []
    for proj_dir in root.iterdir():
        if not proj_dir.is_dir():
            continue
        if is_ephemeral_project_dir(proj_dir.name):
            # Subagent ephemeral run; parent session already captures it.
            continue
        project = _decode_project(proj_dir.name)
        # main session jsonls live directly under project dir
        for f in proj_dir.glob("*.jsonl"):
            try:
                st = f.stat()
            except FileNotFoundError:
                continue
            out.append(
                JsonlFile(
                    path=f,
                    project=project,
                    session_id=f.stem,
                    is_subagent=False,
                    mtime=st.st_mtime,
                    size=st.st_size,
                )
            )
        # subagent jsonls live under <session_id>/subagents/agent-*.jsonl
        for sub in proj_dir.glob("*/subagents/*.jsonl"):
            try:
                st = sub.stat()
            except FileNotFoundError:
                continue
            session_id = sub.parent.parent.name
            out.append(
                JsonlFile(
                    path=sub,
                    project=project,
                    session_id=session_id,
                    is_subagent=True,
                    mtime=st.st_mtime,
                    size=st.st_size,
                )
            )
    return out
