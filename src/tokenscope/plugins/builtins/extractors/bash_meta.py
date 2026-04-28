"""bash_meta — populates bash_program / bash_subcommand / bash_category /
bash_has_sudo / bash_sandbox_disabled on tool_calls rows for Bash invocations.

Reframing of logic that lived inline in ingest._extract_tool_input.
"""
from __future__ import annotations

from ....bash_parse import parse_bash
from ...registry import registry


class BashMeta:
    name = "bash_meta"
    version = "2"
    targets = ("tool_call",)

    def fields(self) -> dict[str, str]:
        return {
            "bash_program": "TEXT",
            "bash_subcommand": "TEXT",
            "bash_category": "TEXT",
            "bash_has_sudo": "INTEGER",
            "bash_sandbox_disabled": "INTEGER",
            "bash_background": "INTEGER",
        }

    def extract(self, rec: dict, ctx) -> dict | None:
        if ctx.tool_name != "Bash":
            return None
        inp = ctx.tool_input or {}
        cmd = inp.get("command") or ""
        parsed = parse_bash(cmd)
        return {
            "bash_program": parsed["program"],
            "bash_subcommand": parsed["subcommand"],
            "bash_category": parsed["category"],
            "bash_has_sudo": parsed["has_sudo"],
            "bash_sandbox_disabled": 1 if inp.get("dangerouslyDisableSandbox") else 0,
            "bash_background": 1 if inp.get("run_in_background") else 0,
        }


registry.register_extractor(BashMeta())
