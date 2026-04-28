# Live Transcript — what tokscope feels like

A representative session with tokscope's MCP server registered.
Numbers and project names below are illustrative; real output reflects
your own data.

---

**You:** *"where am I burning tokens this week?"*

**Claude:** *(calls `get_insights` with `since=7 days ago`)*

> Here's your week:
>
> - **30d up 19% vs prior 30d**, 7d roughly flat. Cache hit ratio 99.94%.
> - **Biggest concern:** `Bash → WebFetch http_error` 16 real errors in 58 calls (`acme-api` project).
> - **Recommendation:** Validate URL or add retry-with-backoff before WebFetch.
>
> Other patterns:
> - 9 sessions hit bash permission denials (top: 22 in `data-pipeline`).
> - 1 session has 7 Agent concurrency races (a1b2c3d4-...).
> - 42 paged-read findings — top is `large_source.cpp` re-read 91× with zero Grep calls.

---

**You:** *"investigate the worst session this week"*

**Claude:** *(calls `investigate(target="auto", filters={since:"..."})`)*

> Session `a1b2c3d4-...` ran **138 hours**, $103.90, 3,722 messages, 2,210 tool calls.
>
> **Key issue:** Agent paged through file with offset Reads instead of Grep. **$1.65 avoidable**.
>
> Root causes (ranked):
> 1. [high] Agent paged through file — $1.65 impact
> 2. [medium] Single turn reached 165k input tokens — context near limit
> 3. [medium] Session ran 138 hours
> 4. [low] Bash dominates 57% of session cost
>
> Actions:
> - **Use Grep for targeted lookup** (high/behavioral): Prefer Grep over offset Read for files >2000 lines.
> - **Split context** (high/behavioral): /compact before fills, split work across sessions.
> - **Start fresh sessions** (medium/behavioral): one task per session.
>
> Top expensive turns: 4 of 8 turns are 0.96–1.46$, all on opus-4-6.

---

**You:** *"why so many WebFetch errors? walk me through it."*

**Claude:** *(calls `find_error_chains(min_n=10)`)*

> Three real WebFetch error chains:
> - `WebFetch → WebFetch` 487 calls, 92 real errors (http_error). Cascading failed fetches.
> - `Bash → WebFetch` 58 calls, 16 real errors. Pattern: bash sets up a URL, immediate WebFetch fails.
> - `Agent → WebFetch` rare (1 error in 6).
>
> All point to **http_error** as the dominant class. The actual sample is
> `"Request failed with status code 404"`.
>
> Recommendation: **validate URLs (curl -I) before WebFetch, or add retry-with-backoff**. The
> `Bash → WebFetch` chain in particular suggests a race — bash creates a resource and the
> WebFetch fires before it's live.

---

**You:** *"compare cache efficiency across models"*

**Claude:** *(calls `get_reasoning_cache(group_by="model")`)*

> | model | cost | hit | cc% | thinking% |
> |---|---|---|---|---|
> | opus-4-6 | $1535 | 99.96% | 98.82% | 0.13% |
> | opus-4-7 | $274 | 99.98% | 99.57% | 0.00% |
> | sonnet-4-6 | $224 | 99.97% | 99.37% | 0.00% |
> | haiku-4-5 | $111 | 99.76% | 97.62% | 0.04% |
>
> All models near-perfect cache hit. Thinking is essentially unused
> across the board — opportunity to enable for harder tasks if quality
> matters more than $.

---

**You:** *"add a custom detector for sessions running more than 12 hours"*

**Claude:** *(reads `examples/sample-detector.py`, edits to threshold=12, writes to `~/.config/tokscope/plugins/long_sessions.py`)*

> Created. Run `tokscope detectors list` and you'll see `long_sessions`
> alongside the built-ins. It'll automatically flow through `get_insights`
> on the next call. To run directly:
>
> ```
> tokscope detectors run long_sessions --param min_hours=12
> ```

---

This is what *making your AI's behaviour observable to itself* looks
like. The agent reasons over its own usage data the same way it'd
reason over any other database — using typed tools that return
contextual evidence + recommendations.
