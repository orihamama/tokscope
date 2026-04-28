"""Ingest pipeline: discover → parse → turn-assemble → attribute → write."""
from __future__ import annotations
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .attribution import byte_size, split_proportional
from .bash_parse import parse_bash
from .db import bump_etag, connect, init_schema, transaction
from .discovery import JsonlFile, discover
from .parser import iter_records
from .plugins import ExtractCtx, registry as plugin_registry
from .pricing import calc_cost

# Tools whose input includes a file path
FILE_TOOLS = {"Read", "Edit", "Write", "MultiEdit"}


def _text_of(content_blob: Any) -> str | None:
    """Best-effort extract a text string from a tool_result content payload.
    Anthropic emits content as either a plain string or a list of blocks
    {type:"text", text:"..."}.
    """
    if content_blob is None:
        return None
    if isinstance(content_blob, str):
        return content_blob
    if isinstance(content_blob, list):
        parts: list[str] = []
        for b in content_blob:
            if isinstance(b, dict):
                t = b.get("text")
                if isinstance(t, str):
                    parts.append(t)
        return "\n".join(parts) if parts else None
    return None


def _ts_to_ms(ts: Any) -> int | None:
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        v = int(ts)
        return v if v > 1e12 else v * 1000
    if isinstance(ts, str):
        try:
            s = ts.replace("Z", "+00:00")
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1000)
        except Exception:
            return None
    return None


def _is_thinking(block: dict) -> bool:
    return isinstance(block, dict) and block.get("type") == "thinking"


def _extract_tool_use_inputs(name: str, inp: dict) -> dict:
    """Pull denormalized columns out of a tool_use input."""
    out: dict = {
        "bash_command": None,
        "bash_background": None,
        "bash_sandbox_disabled": None,
        "bash_program": None,
        "bash_subcommand": None,
        "bash_category": None,
        "bash_pipe_count": None,
        "bash_has_sudo": None,
        "file_path": None,
        "search_pattern": None,
        "agent_subtype": None,
        "agent_description": None,
        "web_url": None,
        "web_query": None,
    }
    if not isinstance(inp, dict):
        return out
    if name == "Bash":
        cmd = inp.get("command") or ""
        out["bash_command"] = cmd[:2000]
        out["bash_background"] = 1 if inp.get("run_in_background") else 0
        out["bash_sandbox_disabled"] = 1 if inp.get("dangerouslyDisableSandbox") else 0
        parsed = parse_bash(cmd)
        out["bash_program"] = parsed["program"]
        out["bash_subcommand"] = parsed["subcommand"]
        out["bash_category"] = parsed["category"]
        out["bash_pipe_count"] = parsed["pipe_count"]
        out["bash_has_sudo"] = parsed["has_sudo"]
    elif name in FILE_TOOLS:
        out["file_path"] = inp.get("file_path")
    elif name == "Glob":
        out["file_path"] = inp.get("path")
        out["search_pattern"] = inp.get("pattern")
    elif name == "Grep":
        out["search_pattern"] = inp.get("pattern")
        out["file_path"] = inp.get("path")
    elif name == "Agent":
        out["agent_subtype"] = inp.get("subagent_type")
        out["agent_description"] = (inp.get("description") or "")[:500]
    elif name == "WebFetch":
        out["web_url"] = inp.get("url")
    elif name == "WebSearch":
        out["web_query"] = (inp.get("query") or "")[:500]
    return out


class FileIngester:
    """Stateful per-file ingester. Handles turn assembly across records."""

    def __init__(self, conn: sqlite3.Connection, jf: JsonlFile):
        self.conn = conn
        self.jf = jf
        # pending_tool_uses: tool_use_id -> dict (open ToolCall awaiting result)
        self.pending: dict[str, dict] = {}
        # last_user_tool_calls: list of tool_use_ids whose results were in the most
        # recent user message → next assistant input_tokens attribute back to them
        self.last_user_tool_calls: list[str] = []
        self.session_id = jf.session_id
        self.project = jf.project

    # ------------------------------------------------------------------
    def ingest(self, start_offset: int) -> tuple[int, str | None]:
        last_offset = start_offset
        last_uuid: str | None = None
        for parsed in iter_records(self.jf.path, start_offset):
            rec = parsed.rec
            try:
                self._process_record(rec)
            except Exception as e:
                # Don't let one bad record stop the file; log to stderr-ish via meta
                self._log_error(f"record error: {e}")
            last_offset = parsed.offset_after
            if rec.get("uuid"):
                last_uuid = rec["uuid"]
        return last_offset, last_uuid

    # ------------------------------------------------------------------
    def _log_error(self, msg: str) -> None:
        # noop for now; could write to meta table
        pass

    # ------------------------------------------------------------------
    def _process_record(self, rec: dict) -> None:
        rtype = rec.get("type")
        if rtype == "assistant":
            self._process_assistant(rec)
        elif rtype == "user":
            self._process_user(rec)
        # other types ignored for analytics

    # ------------------------------------------------------------------
    def _process_assistant(self, rec: dict) -> None:
        msg = rec.get("message") or {}
        usage = msg.get("usage") or {}
        content = msg.get("content") or []
        model = msg.get("model")

        input_tokens = int(usage.get("input_tokens") or 0)
        output_tokens = int(usage.get("output_tokens") or 0)
        cache_creation_raw = usage.get("cache_creation_input_tokens") or 0
        cache_creation = int(cache_creation_raw) if cache_creation_raw else 0
        cache_read = int(usage.get("cache_read_input_tokens") or 0)
        service_tier = usage.get("service_tier")

        thinking_chars = sum(
            len((b or {}).get("thinking", "") or "")
            for b in content
            if _is_thinking(b)
        )
        thinking_tokens = thinking_chars // 4  # rough heuristic

        ts_ms = _ts_to_ms(rec.get("timestamp"))
        cost = calc_cost(
            model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_creation=cache_creation,
            cache_read=cache_read,
        )

        uuid = rec.get("uuid")
        if uuid is None:
            return

        # Anthropic API responses are split across multiple JSONL records — one
        # per content block (thinking / text / tool_use*). Each block carries
        # the SAME usage stats representing the full request. Charging cost on
        # every row inflates spend ~2× (avg 2 blocks/request, up to 19×).
        # Bill exactly one row per (session_id, request_id): the first to land.
        request_id = rec.get("requestId")
        is_billing_row = True
        if request_id:
            existing = self.conn.execute(
                "SELECT 1 FROM messages WHERE session_id=? AND request_id=? "
                "AND role='assistant' LIMIT 1",
                (self.session_id, request_id),
            ).fetchone()
            if existing:
                is_billing_row = False

        b_input = input_tokens if is_billing_row else 0
        b_output = output_tokens if is_billing_row else 0
        b_cc = cache_creation if is_billing_row else 0
        b_cr = cache_read if is_billing_row else 0
        b_think = thinking_tokens if is_billing_row else 0
        b_cost = cost if is_billing_row else 0.0

        self.conn.execute(
            """INSERT OR IGNORE INTO messages
               (uuid, request_id, session_id, project, cwd, git_branch, timestamp,
                role, model, service_tier, permission_mode,
                input_tokens, output_tokens, cache_creation, cache_read,
                thinking_tokens, cost_usd, duration_ms,
                is_sidechain, parent_tool_use_id, tool_use_id,
                is_compact_summary, is_api_error, agent_id, source_file)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                uuid,
                request_id,
                self.session_id,
                self.project,
                rec.get("cwd"),
                rec.get("gitBranch"),
                ts_ms,
                "assistant",
                model,
                service_tier,
                rec.get("permissionMode"),
                b_input,
                b_output,
                b_cc,
                b_cr,
                b_think,
                b_cost,
                rec.get("durationMs"),
                1 if rec.get("isSidechain") else 0,
                rec.get("parentToolUseID"),
                None,
                1 if rec.get("isCompactSummary") else 0,
                1 if rec.get("isApiErrorMessage") else 0,
                rec.get("agentId"),
                str(self.jf.path),
            ),
        )

        # Attribute INPUT tokens of THIS turn back to last_user_tool_calls
        # (those tool_results were what fed this prompt).
        if self.last_user_tool_calls and (input_tokens > 0 or cache_creation > 0):
            self._attribute_input_back(
                self.last_user_tool_calls,
                input_tokens=input_tokens,
                cache_creation=cache_creation,
                cache_read=0,  # cache_read attribution would be session-level, skip
                model=model,
            )
        self.last_user_tool_calls = []

        # Extract tool_use blocks from this assistant message
        tool_use_blocks = [
            b for b in content if isinstance(b, dict) and b.get("type") == "tool_use"
        ]
        if not tool_use_blocks:
            return

        weights = [byte_size(b.get("input")) for b in tool_use_blocks]
        out_split = split_proportional(output_tokens, weights)

        for blk, attr_out in zip(tool_use_blocks, out_split):
            tool_id = blk.get("id")
            tool_name = blk.get("name") or "?"
            inp = blk.get("input") or {}
            denorm = _extract_tool_use_inputs(tool_name, inp)
            attr_cost = calc_cost(model, output_tokens=attr_out)
            self.conn.execute(
                """INSERT OR IGNORE INTO tool_calls
                   (id, message_uuid, tool_name, session_id, project, timestamp,
                    duration_ms, result_bytes, result_lines, result_total_tokens,
                    is_error, interrupted, user_modified, truncated, exit_code,
                    attributed_input_tokens, attributed_output_tokens,
                    attributed_cache_creation, attributed_cache_read,
                    attributed_cost_usd,
                    parent_tool_use_id,
                    bash_command, bash_background, bash_sandbox_disabled,
                    file_path, search_pattern, agent_subtype, agent_description,
                    web_url, web_query, agent_id,
                    bash_program, bash_subcommand, bash_category,
                    bash_pipe_count, bash_has_sudo)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    tool_id,
                    uuid,
                    tool_name,
                    self.session_id,
                    self.project,
                    ts_ms,
                    None, None, None, None,
                    0, 0, 0, 0, None,
                    0, attr_out, 0, 0, attr_cost,
                    rec.get("parentToolUseID"),
                    denorm["bash_command"], denorm["bash_background"], denorm["bash_sandbox_disabled"],
                    denorm["file_path"], denorm["search_pattern"],
                    denorm["agent_subtype"], denorm["agent_description"],
                    denorm["web_url"], denorm["web_query"], None,
                    denorm["bash_program"], denorm["bash_subcommand"], denorm["bash_category"],
                    denorm["bash_pipe_count"], denorm["bash_has_sudo"],
                ),
            )
            self.pending[tool_id] = {
                "weight": weights[tool_use_blocks.index(blk)],
                "model": model,
            }
            # Run plugin extractors over the freshly-inserted tool_call.
            self._apply_tool_extractors(
                rec=rec,
                tool_id=tool_id,
                tool_name=tool_name,
                tool_input=inp,
                tool_result_text=None,
                exit_code=None,
                is_error=False,
                interrupted=False,
            )

    # ------------------------------------------------------------------
    def _process_user(self, rec: dict) -> None:
        """User records carry tool_result blocks → close matching ToolCalls."""
        msg = rec.get("message") or {}
        content = msg.get("content")
        if not isinstance(content, list):
            return
        tool_results = [
            b for b in content
            if isinstance(b, dict) and b.get("type") == "tool_result"
        ]
        if not tool_results:
            return

        tool_use_result = rec.get("toolUseResult") if isinstance(rec.get("toolUseResult"), dict) else {}
        # When user record carries a single tool_result, top-level toolUseResult
        # holds the metadata. When multiple, we still apply it to all (best-effort).
        single = len(tool_results) == 1

        for tr in tool_results:
            tool_id = tr.get("tool_use_id") or tr.get("toolUseId")
            if not tool_id:
                continue
            content_blob = tr.get("content")
            result_bytes = byte_size(content_blob) if content_blob is not None else 0
            is_error = 1 if tr.get("is_error") else 0

            tur = tool_use_result if single else {}
            duration_ms = tur.get("durationMs") or tur.get("totalDurationMs")
            num_lines = tur.get("numLines")
            total_tokens = tur.get("totalTokens")
            interrupted = 1 if tur.get("interrupted") else 0
            user_modified = 1 if tur.get("userModified") else 0
            truncated = 1 if tur.get("truncated") else 0
            exit_code = tur.get("code") if isinstance(tur.get("code"), int) else None
            agent_id = tur.get("agentId")

            self.conn.execute(
                """UPDATE tool_calls
                   SET result_bytes=?, result_lines=?, result_total_tokens=?,
                       is_error=?, interrupted=?, user_modified=?, truncated=?,
                       exit_code=?, duration_ms=COALESCE(?, duration_ms),
                       agent_id=COALESCE(?, agent_id)
                   WHERE id=?""",
                (
                    result_bytes, num_lines, total_tokens,
                    is_error, interrupted, user_modified, truncated,
                    exit_code, duration_ms, agent_id, tool_id,
                ),
            )
            # Plugin extractors get a second pass with the result text + status,
            # so e.g. status_class can classify the tool_result.
            existing_tool_name = self.conn.execute(
                "SELECT tool_name FROM tool_calls WHERE id=?", (tool_id,)
            ).fetchone()
            if existing_tool_name:
                self._apply_tool_extractors(
                    rec=rec,
                    tool_id=tool_id,
                    tool_name=existing_tool_name[0],
                    tool_input=None,
                    tool_result_text=_text_of(content_blob),
                    exit_code=exit_code,
                    is_error=bool(is_error),
                    interrupted=bool(interrupted),
                )
            self.last_user_tool_calls.append(tool_id)

    # ------------------------------------------------------------------
    def _apply_tool_extractors(
        self,
        *,
        rec: dict,
        tool_id: str,
        tool_name: str,
        tool_input: dict | None,
        tool_result_text: str | None,
        exit_code: int | None,
        is_error: bool,
        interrupted: bool,
    ) -> None:
        """Invoke registered tool_call extractors and write any returned
        column values onto the tool_call row. Idempotent — only writes
        non-None values, falling back to existing column data."""
        ctx = ExtractCtx(
            session_id=self.session_id,
            project=self.project,
            source_file=str(self.jf.path),
            target="tool_call",
            tool_name=tool_name,
            tool_use_id=tool_id,
            tool_input=tool_input,
            tool_result_text=tool_result_text,
            exit_code=exit_code,
            is_error=is_error,
            interrupted=interrupted,
        )
        merged: dict[str, Any] = {}
        for ex in plugin_registry.extractors.values():
            if "tool_call" not in ex.targets:
                continue
            try:
                got = ex.extract(rec, ctx)
            except Exception:
                continue
            if not got:
                continue
            merged.update(got)
        if not merged:
            return
        cols = ", ".join(f"{k}=COALESCE(?, {k})" for k in merged)
        self.conn.execute(
            f"UPDATE tool_calls SET {cols} WHERE id=?",
            (*merged.values(), tool_id),
        )

    # ------------------------------------------------------------------
    def _attribute_input_back(
        self,
        tool_ids: list[str],
        input_tokens: int,
        cache_creation: int,
        cache_read: int,
        model: str | None,
    ) -> None:
        # Pull current result_bytes for each tool_id as weights
        rows = self.conn.execute(
            f"SELECT id, COALESCE(result_bytes,0) AS b FROM tool_calls "
            f"WHERE id IN ({','.join('?'*len(tool_ids))})",
            tool_ids,
        ).fetchall()
        if not rows:
            return
        ids = [r["id"] for r in rows]
        weights = [max(int(r["b"]), 1) for r in rows]
        in_split = split_proportional(input_tokens, weights)
        cc_split = split_proportional(cache_creation, weights)
        cr_split = split_proportional(cache_read, weights)
        for tid, ai, acc, acr in zip(ids, in_split, cc_split, cr_split):
            extra_cost = calc_cost(
                model,
                input_tokens=ai,
                cache_creation=acc,
                cache_read=acr,
            )
            self.conn.execute(
                """UPDATE tool_calls
                   SET attributed_input_tokens = COALESCE(attributed_input_tokens,0)+?,
                       attributed_cache_creation = COALESCE(attributed_cache_creation,0)+?,
                       attributed_cache_read = COALESCE(attributed_cache_read,0)+?,
                       attributed_cost_usd = COALESCE(attributed_cost_usd,0)+?
                   WHERE id=?""",
                (ai, acc, acr, extra_cost, tid),
            )


# --------------------------------------------------------------------------
def ingest_all(verbose: bool = False) -> dict:
    conn = connect()
    init_schema(conn)
    files = discover()
    stats = {"files": 0, "skipped": 0, "messages_before": 0, "messages_after": 0, "started": time.time()}
    stats["messages_before"] = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]

    for jf in files:
        row = conn.execute(
            "SELECT mtime, size, last_offset FROM file_state WHERE path=?",
            (str(jf.path),),
        ).fetchone()
        start_offset = 0
        if row is not None:
            if row["size"] == jf.size and row["mtime"] == jf.mtime:
                stats["skipped"] += 1
                continue
            if jf.size < (row["size"] or 0):
                # rotated/truncated → reparse from 0; old rows superseded by ON CONFLICT
                start_offset = 0
            else:
                start_offset = row["last_offset"] or 0

        with transaction(conn):
            ing = FileIngester(conn, jf)
            new_offset, last_uuid = ing.ingest(start_offset)
            conn.execute(
                """INSERT INTO file_state(path, project, session_id, is_subagent,
                       mtime, size, last_offset, last_uuid, updated_at)
                   VALUES(?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(path) DO UPDATE SET
                       mtime=excluded.mtime, size=excluded.size,
                       last_offset=excluded.last_offset, last_uuid=excluded.last_uuid,
                       updated_at=excluded.updated_at""",
                (
                    str(jf.path), jf.project, jf.session_id,
                    1 if jf.is_subagent else 0,
                    jf.mtime, jf.size, new_offset, last_uuid,
                    int(time.time()),
                ),
            )
        stats["files"] += 1
        if verbose:
            print(f"  ingested {jf.path.name} ({jf.size//1024}KB)")

    stats["messages_after"] = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    stats["elapsed_s"] = round(time.time() - stats["started"], 2)
    bump_etag(conn)
    return stats
