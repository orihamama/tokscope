# Contributing to tokenscope

Thanks for taking the time to contribute. tokenscope is a small,
focused tool — bug reports, plugin contributions, and feature ideas
are all welcome.

## Quick start

```bash
git clone https://github.com/orihamama/tokenscope
cd tokenscope
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/pre-commit install      # optional but recommended
.venv/bin/python -m pytest -q     # 44 tests should pass
```

## Layout

| Path | Purpose |
|---|---|
| `src/tokenscope/` | Package source |
| `src/tokenscope/plugins/` | Plugin protocols, registry, built-ins |
| `src/tokenscope/web/` | Static dashboard assets |
| `tests/` | pytest suite + `conftest.py` synthetic-DB fixture |
| `docs/` | Architecture, MCP/CLI references, plugin authoring |
| `examples/` | Drop-in sample detectors + extractors + demo prompts |
| `.github/` | CI, issue/PR templates, dependabot |

## Where to make changes

- **New detector** → `src/tokenscope/plugins/builtins/detectors/<name>.py`
  + import from `detectors/__init__.py`. See [`docs/writing-a-detector.md`](docs/writing-a-detector.md).
- **New extractor** → `src/tokenscope/plugins/builtins/extractors/<name>.py`
  + import from `extractors/__init__.py`. See [`docs/writing-an-extractor.md`](docs/writing-an-extractor.md).
- **New MCP tool** → only when behavior can't be expressed as a detector.
  Add to `mcp_server.py` `list_tools()` and `_payload()`.
- **New entity-level analytic** → `src/tokenscope/analytics_core.py`.
  Detectors should still be plugins.
- **New SQL columns** → declared via `Extractor.fields()`. Don't edit
  `db.py` `SCHEMA` for plugin-driven additions.

## Testing

```bash
.venv/bin/python -m pytest -q                    # all tests
.venv/bin/python -m pytest tests/test_detectors.py  # one file
.venv/bin/python -m pytest -k "paging" -v        # by name
.venv/bin/python -m pytest --cov                 # coverage
```

Every plugin (built-in or PR) needs:
- Protocol conformance test (`test_registry.py` — usually automatic).
- At least one happy-path test against the seeded DB
  (`tests/conftest.py::seeded_db`).
- An empty-DB test to confirm graceful empty-result handling.

## Code style

- `ruff` for lint + format. Configured in `pyproject.toml`.
- `mypy` is non-strict but should not regress.
- 100-char line limit. Type hints on public functions.
- No emojis in code, comments, or commit messages.
- One short comment max per function — only when WHY is non-obvious.

```bash
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy src/
```

## Commit messages

Conventional Commits style:

```
feat(detectors): add long_sessions detector
fix(bash_parse): skip dev/null in touched_files
docs: add architecture diagram
test(investigate): cover auto-target path
```

## Pull requests

1. Branch from `main`.
2. Add tests for new behavior.
3. Update `CHANGELOG.md` under `[Unreleased]`.
4. Update relevant doc in `docs/` if behavior changes.
5. CI must be green (lint + tests on Linux/macOS/Windows × py 3.11/3.12/3.13).

## Reporting bugs

Open an [issue](https://github.com/orihamama/tokenscope/issues) with:

- tokenscope version (`tokenscope --version` or git SHA)
- Python version + OS
- Minimal reproduction (a small SQL query or a synthetic DB seeding,
  not a dump of your real `~/.claude/analytics.db`).
- Expected vs actual behavior.

## Security

For security-sensitive issues see [SECURITY.md](SECURITY.md).
