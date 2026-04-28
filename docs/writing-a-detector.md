# Writing a Detector

A detector is a plugin that runs at query time over the SQLite analytics
DB and returns a list of finding rows. tokenscope ships ~9 built-in
detectors; this guide shows how to add your own.

## Protocol

```python
class Detector(Protocol):
    name: str           # unique key, e.g. "my_detector"
    title: str          # short human-readable title
    description: str    # one-paragraph summary; surfaces as MCP tool description
    params_schema: dict # JSONSchema for params beyond standard filters
    requires: tuple[str, ...]   # extractor / aggregator names this needs

    def run(self, conn, filters: dict, params: dict) -> list[dict]:
        ...
```

`filters` always carries optional keys: `project`, `session_id`,
`task_id`, `tool`, `since`, `until`. Use `tokenscope.analytics_core._build_filters`
to translate them into a `WHERE` clause.

`run` returns a list of dicts. Each row should:

- Carry identifying keys (`session_id`, `file_path`, etc.) so callers can
  drill back into the entity.
- Include `recommendation: str | None` when a known anti-pattern matches.
- Include `result_sample: str | None` (200-char snippet) when surfacing
  error-class findings.

## Minimal example — `slow_bash`

Surface bash commands whose `duration_ms` is above a threshold.

```python
# my_pkg/slow_bash.py
from tokenscope.analytics_core import _build_filters
from tokenscope.plugins import registry


class SlowBash:
    name = "slow_bash"
    title = "Slow bash invocations"
    description = "Bash calls whose duration_ms exceeds the threshold."
    params_schema = {
        "type": "object",
        "properties": {
            "min_ms": {"type": "integer", "minimum": 1, "default": 5000},
        },
    }
    requires = ()

    def run(self, conn, filters, params):
        min_ms = int(params.get("min_ms", 5000))
        _, _, tc_w, tc_p = _build_filters(filters)
        sql = f"""
        SELECT session_id, bash_command, duration_ms, exit_code,
               attributed_cost_usd cost
        FROM tool_calls
        {tc_w + ' AND ' if tc_w else ' WHERE '}
            tool_name='Bash' AND duration_ms >= ?
        ORDER BY duration_ms DESC LIMIT 50
        """
        rows = [dict(r) for r in conn.execute(sql, tc_p + (min_ms,))]
        for r in rows:
            r["recommendation"] = (
                f"Took {r['duration_ms']}ms; consider timeout or async"
                if r["duration_ms"] >= 30000 else None
            )
        return rows


registry.register_detector(SlowBash())
```

## Registering

Three discovery sources, in order:

1. **User dir** — drop the file in `~/.config/tokenscope/plugins/`. Auto-imported.
2. **Entry points** — declare in your package's `pyproject.toml`:
   ```toml
   [project.entry-points."tokenscope.detectors"]
   slow_bash = "my_pkg.slow_bash:SlowBash"
   ```
3. **Built-in** — add module under `src/tokenscope/plugins/builtins/detectors/`
   and import it from `__init__.py`.

After registration, the detector is callable via:

- CLI: `tokenscope detectors run slow_bash --param min_ms=10000`
- MCP: `run_detector(name='slow_bash', params={'min_ms': 10000})`

## Tips

- Reuse `_build_filters` so your detector honours the same filter set as
  built-ins. Otherwise users can't scope your detector by project / since.
- Cap rows at ~50–100. The MCP transport has a token limit and oversize
  payloads get truncated.
- Use `is_user_rejection=0` in `WHERE` clauses to filter user rejections
  from real errors. Add an `include_rejections` boolean param if useful.
- Declare your `requires` honestly. If you depend on a column an extractor
  populates (e.g. `read_offset` from `read_range`), list it. The CLI shows
  required extractors in `tokenscope detectors list`.
