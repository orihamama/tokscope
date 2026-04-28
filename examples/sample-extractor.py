"""Sample user-authored extractor.

Drop this file at ~/.config/tokenscope/plugins/sample_extractor.py to
make it auto-discoverable.

Captures the host portion of WebFetch URLs into a new column for
per-host analytics. After installing, run:

    tokenscope enrich-existing

to backfill the column on existing rows.
"""
from __future__ import annotations

from urllib.parse import urlparse

from tokenscope.plugins import registry


class WebFetchHost:
    name = "webfetch_host"
    version = "1"
    targets = ("tool_call",)

    def fields(self):
        return {"webfetch_host": "TEXT"}

    def extract(self, rec, ctx):
        if ctx.tool_name != "WebFetch":
            return None
        url = (ctx.tool_input or {}).get("url")
        if not url:
            return None
        try:
            host = urlparse(url).hostname
        except Exception:
            return None
        if not host:
            return None
        return {"webfetch_host": host}


registry.register_extractor(WebFetchHost())
