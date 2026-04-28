"""Streaming JSONL line parser with offset tracking and partial-line recovery."""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ParsedLine:
    rec: dict
    offset_after: int  # byte offset of next line start


def iter_records(path: Path, start_offset: int = 0) -> Iterator[ParsedLine]:
    """Yield parsed records starting at byte offset. Stops at first incomplete line."""
    with open(path, "rb") as f:
        f.seek(start_offset)
        buf = b""
        offset = start_offset
        while True:
            chunk = f.read(65536)
            if not chunk:
                break
            buf += chunk
            while True:
                nl = buf.find(b"\n")
                if nl < 0:
                    break
                line = buf[:nl]
                buf = buf[nl + 1 :]
                offset += len(line) + 1
                if not line.strip():
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    # Skip malformed but advance offset
                    continue
                yield ParsedLine(rec=rec, offset_after=offset)
        # buf has leftover incomplete line — do not advance offset past it
