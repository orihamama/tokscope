"""Bash command parser → {program, subcommand, category, pipe_count, has_sudo}."""
from __future__ import annotations
import re
import shlex

CATEGORY = {
    "vcs":       {"git","gh","hg","svn","fossil"},
    "package":   {"npm","pnpm","yarn","bun","pip","pip3","uv","uvx","cargo","poetry",
                  "go","brew","apt","apt-get","dnf","yum","pacman","gem"},
    "python":    {"python","python3","pytest","mypy","ruff","black","ipython","flake8","pyright"},
    "node":      {"node","tsx","ts-node","deno"},
    "file_ops":  {"ls","mkdir","rmdir","rm","mv","cp","touch","ln","chmod","chown","stat","readlink"},
    "text":      {"cat","head","tail","sed","awk","jq","yq","sort","uniq","wc","tr","cut","tee",
                  "diff","cmp","column","paste","fold","fmt","nl"},
    "search":    {"rg","grep","ag","fd","find","locate","ack","which","whereis"},
    "network":   {"curl","wget","ping","nc","ssh","scp","rsync","host","dig","nslookup","traceroute"},
    "build":     {"make","cmake","bazel","gradle","mvn","ninja","scons"},
    "container": {"docker","podman","kubectl","helm","minikube","kind","compose"},
    "db":        {"psql","mysql","sqlite3","redis-cli","mongo","mongosh"},
    "system":    {"ps","top","htop","kill","killall","df","du","free","lsof","uname","uptime","date","env"},
    "shell":     {"echo","printf","export","source","eval","exec","alias","cd","pwd","true","false",
                  "test","[","read","sleep"},
    "archive":   {"tar","zip","unzip","gzip","gunzip","7z","xz"},
    "test":      {"jest","vitest","mocha","tap"},
    "editor":    {"vim","nvim","nano","emacs","code"},
}
PROGRAM_TO_CATEGORY: dict[str, str] = {
    p: cat for cat, progs in CATEGORY.items() for p in progs
}

# Programs whose second positional token is a subcommand worth tracking
HAS_SUBCOMMAND = {
    "git","gh","npm","pnpm","yarn","bun","docker","podman","kubectl","helm",
    "cargo","brew","pip","pip3","uv","uvx","go","poetry","apt","apt-get",
    "dnf","yum","compose",
}

# Per-program flags that take a separate argument; skip both when scanning
# for subcommand. Includes both short and long forms.
FLAGS_WITH_ARG: dict[str, set[str]] = {
    "git":     {"-C","-c","--git-dir","--work-tree","--namespace","--no-replace-objects"},
    "docker":  {"--context","-c","-H","--config","--host","--log-level"},
    "podman":  {"--context","-c","-H","--root","--runroot"},
    "kubectl": {"--context","-n","--namespace","--kubeconfig","--cluster","--user","--server"},
    "gh":      {"--repo","-R","--hostname"},
    "go":      {"-C"},
    "cargo":   {"--manifest-path","-Z","--target-dir","--config"},
    "uv":      {"--directory","--python","--cache-dir"},
    "uvx":     {"--directory","--python","--with"},
    "brew":    {"--prefix","--cellar"},
    "pip":     {"--index-url","-i","--extra-index-url","--cache-dir"},
    "pip3":    {"--index-url","-i","--extra-index-url","--cache-dir"},
    "npm":     {"--prefix","--registry","--workspace","-w"},
    "pnpm":    {"--filter","-F","--dir","-C","--workspace-root","-w"},
    "yarn":    {"--cwd"},
    "bun":     {"--cwd"},
    "helm":    {"--namespace","-n","--kubeconfig"},
}

_ENV_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")

# Shell control-flow keywords; their first "real" command sits after `do` or `then`.
_CONTROL_KEYWORDS = {
    "for", "while", "until", "if", "case", "select", "function", "coproc",
}
# Prefix words that wrap another command without changing its identity.
_PREFIX_WORDS = {"time", "nice", "nohup", "exec", "command", "builtin", "env"}
# Tokens that act as statement bodies — we look past these to find the command.
_BODY_STARTERS = {"do", "then"}
# Tokens we always skip when scanning for a real command.
_SKIP_TOKENS = {"!", "(", "{", "[[", "[", "}", ")", "]]", "fi", "done", "esac", "elif", "else"}


def _count_top_level_pipes(s: str) -> int:
    """Count `|` not inside quotes/escape."""
    in_s = False
    in_d = False
    esc = False
    n = 0
    for ch in s:
        if esc:
            esc = False
            continue
        if ch == "\\":
            esc = True
            continue
        if ch == "'" and not in_d:
            in_s = not in_s
        elif ch == '"' and not in_s:
            in_d = not in_d
        elif ch == "|" and not in_s and not in_d:
            n += 1
    return n


def _strip_leading_comments(s: str) -> str:
    """Drop leading comment lines and blank lines from a multi-line script."""
    lines = s.splitlines()
    out: list[str] = []
    skipping = True
    for ln in lines:
        if skipping:
            t = ln.strip()
            if not t or t.startswith("#"):
                continue
            skipping = False
        out.append(ln)
    return "\n".join(out)


def _split_top_level_statements(s: str) -> list[str]:
    """Split on top-level `;`, `&&`, `||`, newline. Honor quotes."""
    parts: list[str] = []
    buf: list[str] = []
    in_s = in_d = esc = False
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if esc:
            esc = False
            buf.append(ch); i += 1; continue
        if ch == "\\":
            esc = True
            buf.append(ch); i += 1; continue
        if ch == "'" and not in_d:
            in_s = not in_s; buf.append(ch); i += 1; continue
        if ch == '"' and not in_s:
            in_d = not in_d; buf.append(ch); i += 1; continue
        if not in_s and not in_d:
            # newline = statement separator
            if ch == "\n":
                parts.append("".join(buf)); buf = []; i += 1; continue
            if ch == ";":
                parts.append("".join(buf)); buf = []; i += 1; continue
            # && / ||
            if ch == "&" and i + 1 < n and s[i + 1] == "&":
                parts.append("".join(buf)); buf = []; i += 2; continue
            if ch == "|" and i + 1 < n and s[i + 1] == "|":
                parts.append("".join(buf)); buf = []; i += 2; continue
        buf.append(ch); i += 1
    if buf:
        parts.append("".join(buf))
    # Drop leading comments and blanks per segment
    cleaned: list[str] = []
    for p in parts:
        t = p.strip()
        if not t or t.startswith("#"):
            continue
        cleaned.append(t)
    return cleaned


_PROGRAM_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_./+\-]*$")


def _is_program_token(tok: str) -> bool:
    """A token looks like a real program name, not a flag or shell artifact."""
    if not tok:
        return False
    if tok.startswith("-"):
        return False
    if "=" in tok and _ENV_RE.match(tok):
        return False
    # Strip leading path; the basename must match a strict program-name shape.
    base = tok.rsplit("/", 1)[-1] if "/" in tok else tok
    return bool(_PROGRAM_RE.match(base))


def _tokenize(s: str) -> list[str]:
    """shlex with newline-as-whitespace and a forgiving fallback."""
    try:
        return shlex.split(s, posix=True, comments=True)
    except ValueError:
        return s.split()


def _extract_program(toks: list[str]) -> tuple[str | None, list[str]]:
    """Walk tokens skipping env assignments, sudo, control-flow keywords, and
    grouping artifacts. Return (program_token, remaining_tokens_after_it)."""
    i = 0
    n = len(toks)
    while i < n:
        t = toks[i]
        # leading env: VAR=value
        if _ENV_RE.match(t):
            i += 1
            continue
        # sudo and its flags
        if t == "sudo":
            i += 1
            while i < n and toks[i].startswith("-"):
                if toks[i] in ("-u", "-g", "-U", "-C") and i + 1 < n:
                    i += 2
                else:
                    i += 1
            continue
        # prefix words like `time make` — skip just the wrapper.
        if t in _PREFIX_WORDS:
            i += 1
            continue
        # control-flow keywords: scan ahead to body starter
        if t in _CONTROL_KEYWORDS:
            j = i + 1
            while j < n and toks[j] not in _BODY_STARTERS:
                j += 1
            i = j + 1 if j < n else n
            continue
        # explicit body / loop markers
        if t in _BODY_STARTERS or t in _SKIP_TOKENS:
            i += 1
            continue
        # grouping / redirection / heredoc starts → bail out, no real program here
        if not _is_program_token(t):
            i += 1
            continue
        # Found program-shaped token.
        program = t.rsplit("/", 1)[-1] if "/" in t else t
        return program, toks[i + 1:]
    return None, []


def parse_bash(cmd: str | None) -> dict:
    if not cmd:
        return {"program": None, "subcommand": None, "category": None,
                "pipe_count": 0, "has_sudo": 0}
    s = cmd.strip()
    pipe_count = _count_top_level_pipes(s)
    has_sudo = 1 if re.search(r"(^|[;&|\n]\s*)sudo\b", s) else 0
    s_clean = _strip_leading_comments(s)
    statements = _split_top_level_statements(s_clean)
    if not statements:
        return {"program": None, "subcommand": None, "category": "other",
                "pipe_count": pipe_count, "has_sudo": has_sudo}

    program: str | None = None
    after: list[str] = []
    # Walk each top-level statement; first segment to yield a real program wins.
    for stmt in statements:
        # within a statement, take only the first pipe stage for program detection
        head = stmt.split("|", 1)[0]
        toks = _tokenize(head)
        program, after = _extract_program(toks)
        if program:
            break

    if not program:
        return {"program": None, "subcommand": None, "category": "other",
                "pipe_count": pipe_count, "has_sudo": has_sudo}

    subcommand = None
    if program in HAS_SUBCOMMAND:
        flags_with_arg = FLAGS_WITH_ARG.get(program, set())
        i = 0
        while i < len(after):
            t = after[i]
            if t.startswith("--") and "=" in t:
                i += 1
                continue
            if t in flags_with_arg:
                i += 2
                continue
            if t.startswith("-"):
                i += 1
                continue
            subcommand = t
            break
    category = PROGRAM_TO_CATEGORY.get(program, "other")
    return {
        "program": program,
        "subcommand": subcommand,
        "category": category,
        "pipe_count": pipe_count,
        "has_sudo": has_sudo,
    }
