"""The Agent Overlay Specification (AOS) in pure Python.

Everything is importable from here. The library reads bytes, or a ``FileSystem`` the caller
supplies, and returns data or raises ``Error`` carrying the class the specification's
conformance vectors name. It prints nothing, reads no environment variable and runs no process.
"""

from __future__ import annotations

from ._catalog import *  # noqa: F403
from ._compose import *  # noqa: F403
from ._data import *  # noqa: F403
from ._defaults import *  # noqa: F403
from ._digest import *  # noqa: F403
from ._document import *  # noqa: F403
from ._dotenv import *  # noqa: F403
from ._effective import *  # noqa: F403
from ._env import *  # noqa: F403
from ._errors import *  # noqa: F403
from ._front_matter import *  # noqa: F403
from ._fs import *  # noqa: F403
from ._hooks import *  # noqa: F403
from ._icon import *  # noqa: F403
from ._names import *  # noqa: F403
from ._settings import *  # noqa: F403
from ._skills import *  # noqa: F403
from ._source import *  # noqa: F403
from ._subst import *  # noqa: F403
from ._tree import *  # noqa: F403
from ._validate import *  # noqa: F403
