## Summary

<!-- What changes and why. Keep tight. -->

## Surface(s) touched

- [ ] MCP server (tool schemas / behavior)
- [ ] CLI
- [ ] Web dashboard
- [ ] Ingest / DB / aggregation
- [ ] Docs / packaging / CI

## Test plan

<!-- Bulleted steps. How did you verify this works? -->

- [ ] `pytest` passes
- [ ] `ruff check` clean
- [ ] If MCP tool schemas changed: ran `npx @modelcontextprotocol/inspector` and confirmed `tools/list`
- [ ] Manual smoke for affected surface

## Breaking changes

- [ ] None
- [ ] DB schema (note migration path)
- [ ] MCP tool schema (note in CHANGELOG)
- [ ] CLI flags
