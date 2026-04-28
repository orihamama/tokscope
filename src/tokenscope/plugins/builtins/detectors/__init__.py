"""Built-in detectors. Each module registers itself on import."""
from . import agent_races  # noqa: F401
from . import bash_retries  # noqa: F401
from . import dead_search_patterns  # noqa: F401
from . import duplicate_reads  # noqa: F401
from . import error_chains  # noqa: F401
from . import paging_reads  # noqa: F401
from . import permission_denials  # noqa: F401
from . import redundant_read_ranges  # noqa: F401
from . import repeat_tasks  # noqa: F401
