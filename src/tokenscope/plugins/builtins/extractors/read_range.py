"""read_range — captures Read tool offset and limit from input args."""
from __future__ import annotations

from ...registry import registry


class ReadRange:
    name = "read_range"
    version = "1"
    targets = ("tool_call",)

    def fields(self) -> dict[str, str]:
        return {
            "read_offset": "INTEGER",
            "read_limit": "INTEGER",
        }

    def extract(self, rec: dict, ctx) -> dict | None:
        if ctx.tool_name != "Read":
            return None
        inp = ctx.tool_input or {}
        offset = inp.get("offset")
        limit = inp.get("limit")
        if offset is None and limit is None:
            return None
        return {
            "read_offset": int(offset) if offset is not None else None,
            "read_limit": int(limit) if limit is not None else None,
        }


registry.register_extractor(ReadRange())
