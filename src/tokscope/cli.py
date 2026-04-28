"""Typer CLI: ingest / report / serve / export."""

from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.table import Table

from . import aggregate
from . import ingest as ingest_mod
from .db import connect, init_schema
from .paths import DB_PATH

app = typer.Typer(help="Claude Code token analytics — per-tool / per-task / per-project.")
console = Console()


@app.command("ingest")
def ingest_cmd(verbose: bool = typer.Option(False, "--verbose", "-v")):
    """Scan ~/.claude/projects/, parse JSONL files, update SQLite DB."""
    console.print("[bold]Ingesting...[/]")
    stats = ingest_mod.ingest_all(verbose=verbose)
    console.print(f"  files processed: [green]{stats['files']}[/], skipped: {stats['skipped']}")
    new_msgs = stats["messages_after"] - stats["messages_before"]
    console.print(
        f"  messages: {stats['messages_before']} → {stats['messages_after']} (+{new_msgs})"
    )
    console.print(f"  elapsed: {stats['elapsed_s']}s")
    console.print("[bold]Aggregating...[/]")
    agg = aggregate.rebuild_all()
    console.print(
        f"  sessions={agg['sessions']} tasks={agg['tasks']} "
        f"file_activity={agg['file_activity']} sequences={agg['tool_sequences']}"
    )
    console.print(f"[dim]DB at {DB_PATH}[/]")


@app.command("report")
def report_cmd(
    by: str = typer.Option("tool", "--by", help="tool|task|project|session|day|file|bash|workflow"),
    limit: int = typer.Option(20, "--limit", "-n"),
    project: str | None = typer.Option(None, "--project", "-p"),
):
    """Print analytics tables to terminal."""
    conn = connect()
    init_schema(conn)
    if by == "tool":
        sql = """SELECT tool_name, COUNT(*) calls, SUM(is_error) errors,
                        ROUND(AVG(NULLIF(duration_ms,0)),0) avg_ms,
                        SUM(attributed_input_tokens) in_tok,
                        SUM(attributed_output_tokens) out_tok,
                        ROUND(SUM(attributed_cost_usd),4) cost_usd
                 FROM tool_calls"""
        if project:
            sql += " WHERE project=?"
            params = (project,)
        else:
            params = ()
        sql += " GROUP BY tool_name ORDER BY cost_usd DESC LIMIT ?"
        rows = conn.execute(sql, params + (limit,)).fetchall()
        _table("Tools by cost", rows)
    elif by == "task":
        rows = conn.execute(
            """SELECT agent_type, COUNT(*) tasks,
                      ROUND(AVG(duration_ms)/1000.0,1) avg_s,
                      SUM(message_count) msgs,
                      ROUND(SUM(total_cost_usd),4) cost_usd
               FROM tasks GROUP BY agent_type ORDER BY cost_usd DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        _table("Tasks by agent type", rows)
        rows = conn.execute(
            """SELECT root_tool_use_id, agent_type, SUBSTR(description,1,60) description,
                      message_count, tool_call_count,
                      ROUND(total_cost_usd,4) cost_usd, project
               FROM tasks ORDER BY total_cost_usd DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        _table("Top expensive tasks", rows)
    elif by == "project":
        rows = conn.execute(
            """SELECT project, COUNT(*) sessions,
                      SUM(message_count) msgs, SUM(tool_call_count) tools,
                      ROUND(SUM(total_cost_usd),4) cost_usd,
                      ROUND(AVG(cache_hit_ratio),3) cache_hit
               FROM sessions GROUP BY project ORDER BY cost_usd DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        _table("Projects", rows)
    elif by == "session":
        rows = conn.execute(
            """SELECT session_id, project, message_count msgs, tool_call_count tools,
                      compaction_count compactions, error_count errs,
                      ROUND(total_cost_usd,4) cost_usd,
                      ROUND(cache_hit_ratio,3) cache_hit
               FROM sessions ORDER BY total_cost_usd DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        _table("Sessions by cost", rows)
    elif by == "day":
        rows = conn.execute(
            """SELECT day, ROUND(SUM(cost_usd),4) cost_usd,
                      SUM(input_tokens) in_tok, SUM(output_tokens) out_tok,
                      SUM(cache_read) cache_read, SUM(cache_creation) cache_create,
                      COUNT(DISTINCT project) projects
               FROM v_daily_spend GROUP BY day ORDER BY day DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        _table("Daily spend", rows)
    elif by == "file":
        rows = conn.execute(
            """SELECT file_path, SUM(reads) reads, SUM(edits) edits, SUM(writes) writes,
                      ROUND(SUM(total_cost_usd),4) cost_usd
               FROM file_activity GROUP BY file_path
               ORDER BY (SUM(reads)+SUM(edits)+SUM(writes)) DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        _table("File hotspots", rows)
    elif by == "bash":
        rows = conn.execute(
            """SELECT SUBSTR(bash_command,1,60) command, COUNT(*) n,
                      SUM(is_error) errs,
                      ROUND(SUM(attributed_cost_usd),4) cost_usd
               FROM tool_calls WHERE tool_name='Bash' AND bash_command IS NOT NULL
               GROUP BY SUBSTR(bash_command,1,60)
               ORDER BY n DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        _table("Top Bash commands", rows)
    elif by == "workflow":
        rows = conn.execute(
            """SELECT prev_tool, next_tool, SUM(count) n
               FROM tool_sequences GROUP BY prev_tool, next_tool
               ORDER BY n DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        _table("Tool sequences (bigrams)", rows)
    else:
        console.print(f"[red]Unknown --by value: {by}[/]")
        raise typer.Exit(2)


@app.command("export")
def export_cmd(
    table: str = typer.Argument(..., help="messages|tool_calls|tasks|sessions|file_activity"),
    fmt: str = typer.Option("json", "--format", "-f", help="json|csv"),
    output: str | None = typer.Option(None, "--output", "-o"),
):
    conn = connect()
    init_schema(conn)
    rows = conn.execute(f"SELECT * FROM {table}").fetchall()
    cols = [d[0] for d in conn.execute(f"SELECT * FROM {table} LIMIT 0").description]
    if fmt == "json":
        data = [dict(r) for r in rows]
        text = json.dumps(data, default=str, indent=2)
    elif fmt == "csv":
        import csv
        import io

        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(cols)
        for r in rows:
            w.writerow([r[c] for c in cols])
        text = buf.getvalue()
    else:
        console.print(f"[red]Unknown format: {fmt}[/]")
        raise typer.Exit(2)
    if output:
        from pathlib import Path

        Path(output).write_text(text)
        console.print(f"wrote {len(rows)} rows → {output}")
    else:
        console.print(text)


@app.command("serve")
def serve_cmd(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8787, "--port"),
    no_watch: bool = typer.Option(False, "--no-watch"),
):
    """Start web dashboard at http://host:port. Auto-ingests on file changes."""
    import uvicorn

    from .server import app as fastapi_app
    from .server import start_watcher

    if not no_watch:
        start_watcher()
    uvicorn.run(fastapi_app, host=host, port=port, log_level="info")


@app.command("enrich-existing")
def enrich_existing_cmd():
    """Backfill plugin extractor columns over existing tool_call rows.

    Walks every JSONL file referenced by `messages.source_file`, extracts
    tool_use input + tool_result text per call, runs all registered
    extractors, and UPDATEs the existing tool_call rows with the results.
    Idempotent — extractors only fill missing values."""
    import json as _json
    from pathlib import Path as _Path

    from .plugins import ExtractCtx
    from .plugins import registry as _registry

    _registry.load_all()
    conn = connect()
    init_schema(conn)
    # Map source_file -> set of tool_use_ids we need to refresh.
    files = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT source_file FROM messages WHERE source_file IS NOT NULL"
        ).fetchall()
    ]
    console.print(f"Scanning [bold]{len(files)}[/] JSONL files...")
    updated_inserts = 0
    updated_results = 0
    for sf in files:
        p = _Path(sf)
        if not p.exists():
            continue
        # Build tool_use input map and tool_result text map by walking file.
        try:
            lines = p.read_text(errors="ignore").splitlines()
        except Exception:
            continue
        for line in lines:
            try:
                rec = _json.loads(line)
            except Exception:
                continue
            rtype = rec.get("type")
            session_id = rec.get("sessionId") or ""
            project = rec.get("cwd") or ""
            msg = rec.get("message") or {}
            content = msg.get("content") if isinstance(msg.get("content"), list) else []
            if rtype == "assistant":
                for blk in content:
                    if not isinstance(blk, dict) or blk.get("type") != "tool_use":
                        continue
                    tool_id = blk.get("id")
                    if not tool_id:
                        continue
                    row = conn.execute(
                        "SELECT tool_name FROM tool_calls WHERE id=?", (tool_id,)
                    ).fetchone()
                    if not row:
                        continue
                    ctx = ExtractCtx(
                        session_id=session_id,
                        project=project,
                        source_file=str(p),
                        target="tool_call",
                        tool_name=row[0],
                        tool_use_id=tool_id,
                        tool_input=blk.get("input") or {},
                    )
                    merged = _run_extractors(ctx, rec)
                    if merged:
                        cols = ", ".join(f"{k}=COALESCE(?, {k})" for k in merged)
                        conn.execute(
                            f"UPDATE tool_calls SET {cols} WHERE id=?", (*merged.values(), tool_id)
                        )
                        updated_inserts += 1
            elif rtype == "user":
                for blk in content:
                    if not isinstance(blk, dict) or blk.get("type") != "tool_result":
                        continue
                    tool_id = blk.get("tool_use_id") or blk.get("toolUseId")
                    if not tool_id:
                        continue
                    row = conn.execute(
                        "SELECT tool_name, exit_code, is_error, interrupted "
                        "FROM tool_calls WHERE id=?",
                        (tool_id,),
                    ).fetchone()
                    if not row:
                        continue
                    raw = blk.get("content")
                    text = raw if isinstance(raw, str) else None
                    if isinstance(raw, list):
                        text = (
                            "\n".join(x.get("text", "") for x in raw if isinstance(x, dict)) or None
                        )
                    ctx = ExtractCtx(
                        session_id=session_id,
                        project=project,
                        source_file=str(p),
                        target="tool_call",
                        tool_name=row[0],
                        tool_use_id=tool_id,
                        tool_result_text=text,
                        exit_code=row[1],
                        is_error=bool(row[2]),
                        interrupted=bool(row[3]),
                    )
                    merged = _run_extractors(ctx, rec)
                    if merged:
                        cols = ", ".join(f"{k}=COALESCE(?, {k})" for k in merged)
                        conn.execute(
                            f"UPDATE tool_calls SET {cols} WHERE id=?", (*merged.values(), tool_id)
                        )
                        updated_results += 1
        conn.commit()
    console.print(
        f"  updated {updated_inserts} tool_use enrichments, "
        f"{updated_results} tool_result enrichments"
    )


def _run_extractors(ctx, rec):
    from .plugins import registry as _registry

    merged: dict = {}
    for ex in _registry.extractors.values():
        if "tool_call" not in ex.targets:
            continue
        try:
            got = ex.extract(rec, ctx)
        except Exception:
            continue
        if got:
            merged.update(got)
    return merged


@app.command("dedupe-billing")
def dedupe_billing_cmd():
    """Fix double-billing: zero token/cost fields on duplicate (session_id, request_id)
    assistant rows. Anthropic emits one JSONL record per content block within a
    response; all carry identical usage. Only the earliest row should bill."""
    conn = connect()
    init_schema(conn)
    before = conn.execute(
        "SELECT ROUND(SUM(cost_usd),2) FROM messages WHERE role='assistant'"
    ).fetchone()[0]
    n = conn.execute("""
      WITH ranked AS (
        SELECT uuid,
          ROW_NUMBER() OVER (PARTITION BY session_id, request_id ORDER BY timestamp, uuid) rn
        FROM messages WHERE role='assistant' AND request_id IS NOT NULL)
      UPDATE messages
      SET input_tokens=0, output_tokens=0, cache_creation=0, cache_read=0,
          thinking_tokens=0, cost_usd=0
      WHERE uuid IN (SELECT uuid FROM ranked WHERE rn>1)
    """).rowcount
    conn.commit()
    after = conn.execute(
        "SELECT ROUND(SUM(cost_usd),2) FROM messages WHERE role='assistant'"
    ).fetchone()[0]
    console.print(f"  zeroed {n} duplicate billing rows")
    console.print(f"  total cost: ${before:,.2f} → ${after:,.2f} (saved ${before - after:,.2f})")


@app.command("prune-ephemeral")
def prune_ephemeral_cmd():
    """Remove ephemeral subagent rows (/private/tmp/agent/*) and re-attribute
    worktree projects (.../claude/worktrees/*) back to their parent project."""
    conn = connect()
    init_schema(conn)
    counts: dict[str, int] = {}
    for tbl in ("messages", "tool_calls", "sessions", "tasks", "file_activity"):
        # Detect column name for project filter
        cols = {c[1] for c in conn.execute(f"PRAGMA table_info({tbl})").fetchall()}
        if "project" not in cols:
            continue
        n = conn.execute(f"DELETE FROM {tbl} WHERE project LIKE '/private/tmp/agent/%'").rowcount
        counts[f"{tbl} eph"] = n
    # Worktree paths: rewrite project to parent.
    rewrite_sql = (
        "UPDATE {tbl} SET project = SUBSTR(project, 1, INSTR(project, '/.claude/worktrees/')-1) "
        "WHERE project LIKE '%/.claude/worktrees/%'"
    )
    rewrite_sql_alt = (
        "UPDATE {tbl} SET project = SUBSTR(project, 1, INSTR(project, '//claude/worktrees/')-1) "
        "WHERE project LIKE '%//claude/worktrees/%'"
    )
    for tbl in ("messages", "tool_calls", "sessions", "tasks", "file_activity"):
        cols = {c[1] for c in conn.execute(f"PRAGMA table_info({tbl})").fetchall()}
        if "project" not in cols:
            continue
        n1 = conn.execute(rewrite_sql.format(tbl=tbl)).rowcount
        n2 = conn.execute(rewrite_sql_alt.format(tbl=tbl)).rowcount
        counts[f"{tbl} worktree"] = n1 + n2
    conn.execute("DELETE FROM file_state WHERE path LIKE '%/-private-tmp-agent-%'")
    conn.commit()
    for k, v in counts.items():
        if v:
            console.print(f"  {k}: {v}")
    console.print("[green]Done.[/]")


@app.command("reparse-bash")
def reparse_bash_cmd(
    batch: int = typer.Option(2000, "--batch"),
):
    """Re-run the bash parser on every stored Bash tool_call. Use after parser changes."""
    from .bash_parse import parse_bash

    conn = connect()
    init_schema(conn)
    total = conn.execute(
        "SELECT COUNT(*) FROM tool_calls WHERE tool_name='Bash' AND bash_command IS NOT NULL"
    ).fetchone()[0]
    console.print(f"Re-parsing [bold]{total}[/] bash tool_calls...")
    updated = 0
    offset = 0
    while True:
        rows = conn.execute(
            "SELECT id, bash_command FROM tool_calls "
            "WHERE tool_name='Bash' AND bash_command IS NOT NULL "
            "ORDER BY rowid LIMIT ? OFFSET ?",
            (batch, offset),
        ).fetchall()
        if not rows:
            break
        for r in rows:
            p = parse_bash(r["bash_command"])
            conn.execute(
                "UPDATE tool_calls SET bash_program=?, bash_subcommand=?, "
                "bash_category=?, bash_has_sudo=? WHERE id=?",
                (p["program"], p["subcommand"], p["category"], p["has_sudo"], r["id"]),
            )
        conn.commit()
        updated += len(rows)
        offset += len(rows)
        console.print(f"  {updated}/{total}")
    console.print(f"[green]Done.[/] Updated {updated} rows.")


detectors_app = typer.Typer(help="List and run registered detectors.")
extractors_app = typer.Typer(help="List registered extractors.")
app.add_typer(detectors_app, name="detectors")
app.add_typer(extractors_app, name="extractors")


@detectors_app.command("list")
def detectors_list_cmd():
    from .plugins import registry as _registry

    _registry.load_all()
    if not _registry.detectors:
        console.print("[yellow]no detectors registered[/]")
        return
    t = Table(title="Detectors", show_lines=False)
    t.add_column("name")
    t.add_column("title")
    t.add_column("requires")
    for d in _registry.detectors.values():
        t.add_row(d.name, d.title, ", ".join(d.requires) or "—")
    console.print(t)


@detectors_app.command("run")
def detectors_run_cmd(
    name: str = typer.Argument(..., help="Detector name. See `detectors list`."),
    project: str | None = typer.Option(None, "--project", "-p"),
    session_id: str | None = typer.Option(None, "--session"),
    since: str | None = typer.Option(None, "--since"),
    until: str | None = typer.Option(None, "--until"),
    param: list[str] = typer.Option([], "--param", help="key=value (repeatable)"),
):
    from .plugins import registry as _registry

    _registry.load_all()
    if name not in _registry.detectors:
        console.print(f"[red]unknown detector: {name}[/]")
        console.print(f"available: {sorted(_registry.detectors)}")
        raise typer.Exit(2)
    params: dict = {}
    for p in param:
        if "=" in p:
            k, v = p.split("=", 1)
            try:
                v = int(v)
            except ValueError:
                try:
                    v = float(v)
                except ValueError:
                    pass
            params[k.strip()] = v
    filters = {
        k: v
        for k, v in {
            "project": project,
            "session_id": session_id,
            "since": since,
            "until": until,
        }.items()
        if v
    }
    conn = connect()
    init_schema(conn)
    rows = _registry.detectors[name].run(conn, filters, params)
    if not rows:
        console.print(f"[yellow]{name}: no findings[/]")
        return
    cols = sorted({k for r in rows for k in r}, key=lambda c: (c != "session_id", c))
    t = Table(title=f"{name} ({len(rows)} findings)", show_lines=False)
    for c in cols:
        t.add_column(c, overflow="fold")
    for r in rows[:50]:
        t.add_row(*[str(r.get(c, "")) if r.get(c) is not None else "" for c in cols])
    console.print(t)


@extractors_app.command("list")
def extractors_list_cmd():
    from .plugins import registry as _registry

    _registry.load_all()
    if not _registry.extractors:
        console.print("[yellow]no extractors registered[/]")
        return
    t = Table(title="Extractors", show_lines=False)
    t.add_column("name")
    t.add_column("version")
    t.add_column("targets")
    t.add_column("fields")
    for e in _registry.extractors.values():
        t.add_row(e.name, e.version, ", ".join(e.targets), ", ".join(e.fields()))
    console.print(t)


@app.command("mcp")
def mcp_cmd():
    """Run MCP stdio server. Configure in Claude Code / Claude Desktop.

    Claude Code:
        claude mcp add tokscope /path/to/.venv/bin/tokscope mcp

    Claude Desktop (~/Library/Application Support/Claude/claude_desktop_config.json):
        {"mcpServers": {"tokscope":
            {"command": "/path/to/.venv/bin/tokscope", "args": ["mcp"]}}}
    """
    import asyncio

    from .mcp_server import main

    asyncio.run(main())


def _table(title: str, rows):
    if not rows:
        console.print(f"[yellow]{title}: no rows[/]")
        return
    cols = list(rows[0].keys())
    t = Table(title=title, show_lines=False)
    for c in cols:
        t.add_column(c, overflow="fold")
    for r in rows:
        t.add_row(*[str(r[c]) if r[c] is not None else "" for c in cols])
    console.print(t)


if __name__ == "__main__":
    app()
