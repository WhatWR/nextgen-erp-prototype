"""NextGen Agent Runtime.

A separately deployed orchestration microservice. It owns model calls, prompt
and agent versions, and the tool loop. It owns no ERP data: every read,
calculation, policy decision, proposal and write goes through the versioned
Frappe agent gateway.
"""

from __future__ import annotations

__version__ = "0.1.0"
RUNTIME_VERSION = __version__
