"""Check registry.

Importing this package registers every check. Adding a new one means creating
a module here and importing it below — nothing else in the codebase changes.
"""

from .base import (  # noqa: F401
    BaseCheck,
    Check,
    CheckContext,
    REGISTRY,
    SkippedCheck,
    all_checks,
    register,
    run_all,
)

# Import order is irrelevant; ids are sorted at run time.
from . import custom  # noqa: F401,E402
from . import description  # noqa: F401,E402
from . import links  # noqa: F401,E402
from . import metadata  # noqa: F401,E402
from . import playlists  # noqa: F401,E402
from . import risk  # noqa: F401,E402

__all__ = [
    "BaseCheck", "Check", "CheckContext", "REGISTRY", "SkippedCheck",
    "all_checks", "register", "run_all",
]
