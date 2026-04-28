"""Built-in detectors. Each module registers itself on import."""

from . import (
    agent_races,  # noqa: F401
    bash_retries,  # noqa: F401
    dead_search_patterns,  # noqa: F401
    duplicate_reads,  # noqa: F401
    error_chains,  # noqa: F401
    paging_reads,  # noqa: F401
    permission_denials,  # noqa: F401
    redundant_read_ranges,  # noqa: F401
    repeat_tasks,  # noqa: F401
)
