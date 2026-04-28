# Security Policy

## Supported versions

The latest minor version receives security fixes. Older versions are
not patched.

## Threat model

tokenscope is a **read-only local analytics tool**. It:

- Reads files only under `~/.claude/projects/`.
- Writes only to `~/.claude/analytics.db` (a single SQLite file).
- Does not make network calls except an optional weekly LiteLLM pricing
  refresh (HTTPS, no telemetry).

The threat surface is therefore small but non-zero:

1. **Plugin code execution.** User-installed extractors and detectors
   are imported and executed by tokenscope. Plugins from
   `~/.config/tokenscope/plugins/` and entry-points run with the same
   privileges as tokenscope itself. **Only install plugins you trust.**

2. **Data sensitivity.** `~/.claude/analytics.db` may contain
   command-line arguments, file paths, search patterns, and excerpts of
   tool errors from your sessions. Avoid sharing the DB or its exports
   if they contain confidential data. The `result_text_snippet` column
   stores the first 200 characters of error messages — this can include
   sensitive payloads in some scenarios.

3. **MCP exposure.** When registered as an MCP server, tokenscope
   responds to any caller on the same machine. The Claude Code / Claude
   Desktop runtime is the typical caller. There is no auth — local
   stdio only. Do not expose tokenscope over a network without
   wrapping it in an authenticated layer.

## Reporting a vulnerability

Please **do not** open a public issue for a security report.

Email `orihh8@gmail.com` with:

- Description of the issue
- Steps to reproduce
- Affected versions
- Any suggested mitigation

You'll get an acknowledgement within 7 days. We aim to ship a fix
within 30 days for confirmed issues, sooner for high-severity.

## Out of scope

- Issues caused by user-installed third-party plugins.
- Behavior controlled by the underlying SQLite engine or Anthropic
  JSONL format itself.
- Local privilege escalation that requires existing write access to
  `~/.claude/`.
