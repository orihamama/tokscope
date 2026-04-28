"""bash_touched_files — parses Bash command for file paths the command writes to.

Heuristics: `>FILE`, `>>FILE`, `mv X Y` → Y, `cp X Y` → Y, `touch X`,
`mkdir -p X`, `tee X`, `rm X`, `git restore/checkout X`, `2>FILE`.

Stored as JSON array of paths in `tool_calls.touched_files`.
"""

from __future__ import annotations

import json
import re
import shlex

from ...registry import registry

# Programs whose 2nd arg (or all positional args) are write targets.
_WRITE_PROGRAMS_FIRST_ARG = {"touch", "mkdir", "rm", "rmdir", "ln"}
_WRITE_PROGRAMS_SECOND_ARG = {"mv", "cp", "tee"}
_REDIRECT_RE = re.compile(r"(?:^|\s)(?:\d*>>?|2>)\s*([^\s|;&<>]+)")


def _shell_split(s: str) -> list[str]:
    try:
        return shlex.split(s, posix=True, comments=True)
    except ValueError:
        return s.split()


def extract_touched_files(cmd: str) -> list[str]:
    """Return list of paths the command writes to or modifies."""
    if not cmd:
        return []
    out: list[str] = []
    # Redirects (>, >>, 2>) — top-level scan
    for m in _REDIRECT_RE.findall(cmd):
        if m and not m.startswith("&"):
            out.append(m)
    # Walk each top-level statement (split on ; && ||)
    for stmt in re.split(r";|&&|\|\||\n", cmd):
        s = stmt.strip()
        if not s or s.startswith("#"):
            continue
        toks = _shell_split(s.split("|", 1)[0])
        # strip env, sudo, control keywords
        i = 0
        while i < len(toks):
            t = toks[i]
            if "=" in t and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", t):
                i += 1
                continue
            if t == "sudo":
                i += 1
                while i < len(toks) and toks[i].startswith("-"):
                    i += 1
                continue
            if t in {
                "for",
                "while",
                "until",
                "if",
                "case",
                "do",
                "then",
                "select",
                "function",
                "time",
                "nice",
                "nohup",
                "exec",
                "env",
            }:
                i += 1
                continue
            break
        if i >= len(toks):
            continue
        program = toks[i].rsplit("/", 1)[-1]
        rest = toks[i + 1 :]
        # skip flags
        positional = [x for x in rest if not x.startswith("-")]
        if program in _WRITE_PROGRAMS_FIRST_ARG and positional:
            out.extend(positional)
        elif program in _WRITE_PROGRAMS_SECOND_ARG and len(positional) >= 2:
            out.append(positional[-1])
        elif program == "git" and len(positional) >= 2 and positional[0] in {"restore", "checkout"}:
            out.extend(positional[1:])
    # Dedupe preserving order; drop pure flags, shell artifacts, noisy
    # destinations like /dev/null.
    noise = {"/dev/null", "/dev/stderr", "/dev/stdout", "&1", "&2"}
    seen: set[str] = set()
    cleaned: list[str] = []
    for p in out:
        if not p or p.startswith("-") or p in noise or p in seen:
            continue
        seen.add(p)
        cleaned.append(p)
    return cleaned[:20]


class BashTouchedFiles:
    name = "bash_touched_files"
    version = "1"
    targets = ("tool_call",)

    def fields(self) -> dict[str, str]:
        return {"touched_files": "TEXT"}

    def extract(self, rec: dict, ctx) -> dict | None:
        if ctx.tool_name != "Bash":
            return None
        inp = ctx.tool_input or {}
        cmd = inp.get("command") or ""
        files = extract_touched_files(cmd)
        if not files:
            return None
        return {"touched_files": json.dumps(files)}


registry.register_extractor(BashTouchedFiles())
