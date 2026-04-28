"""status_class — classifies tool_result text into a status_class enum.

Distinguishes user rejections from real errors so detectors can filter cleanly.
Stores a 200-char snippet for downstream debugging.
"""

from __future__ import annotations

import re

from ...registry import registry

_STATUS_VALUES = {
    "success",
    "user_rejection",
    "denied",
    "not_in_plan_mode",
    "agent_busy",
    "http_error",
    "timeout",
    "bash_exit_nonzero",
    "empty_result",
    "interrupted",
    "tool_error",
}

_HTTP_RE = re.compile(r"Request failed with status code (\d+)")


def classify(
    text: str | None,
    tool_name: str | None,
    exit_code: int | None,
    is_error: bool,
    interrupted: bool,
) -> tuple[str, int]:
    """Return (status_class, is_user_rejection)."""
    t = (text or "")[:500]
    if interrupted:
        return ("interrupted", 0)
    if "user doesn't want to proceed" in t or "tool use was rejected" in t:
        return ("user_rejection", 1)
    if not is_error:
        if tool_name in {"Grep", "Glob"} and (text or "").strip().startswith("No matches"):
            return ("empty_result", 0)
        return ("success", 0)
    # is_error == True from here on.
    if (
        "Permission to use" in t
        or "permission has been denied" in t.lower()
        or "permission denied" in t.lower()
    ):
        return ("denied", 0)
    if "You are not in plan mode" in t:
        return ("not_in_plan_mode", 0)
    if "still running. Use TaskStop" in t:
        return ("agent_busy", 0)
    if _HTTP_RE.search(t):
        return ("http_error", 0)
    if tool_name == "Bash":
        if exit_code in (124,):
            return ("timeout", 0)
        if exit_code and exit_code != 0:
            return ("bash_exit_nonzero", 0)
    return ("tool_error", 0)


class StatusClass:
    name = "status_class"
    version = "1"
    targets = ("tool_call",)

    def fields(self) -> dict[str, str]:
        return {
            "status_class": "TEXT",
            "is_user_rejection": "INTEGER",
            "result_text_snippet": "TEXT",
        }

    def extract(self, rec: dict, ctx) -> dict | None:
        if ctx.target != "tool_call":
            return None
        text = ctx.tool_result_text
        cls, rej = classify(text, ctx.tool_name, ctx.exit_code, ctx.is_error, ctx.interrupted)
        snippet = (text or "")[:200] if text and (ctx.is_error or ctx.interrupted) else None
        return {
            "status_class": cls,
            "is_user_rejection": rej,
            "result_text_snippet": snippet,
        }


registry.register_extractor(StatusClass())
