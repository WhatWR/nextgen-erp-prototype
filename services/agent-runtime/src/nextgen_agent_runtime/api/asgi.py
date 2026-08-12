"""ASGI entrypoint.

Run with any ASGI server supplied by the deployment image, for example::

    uvicorn nextgen_agent_runtime.api.asgi:application --host 127.0.0.1 --port 8400

Bind to the private service network only. Frappe is the sole client.
"""

from __future__ import annotations

from .app import create_app

application = create_app()
