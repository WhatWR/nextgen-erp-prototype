"""Shared test doubles: an in-process ASGI client and a fake Frappe gateway."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from nextgen_agent_runtime.config import RuntimeConfig
from nextgen_agent_runtime.models.contracts import CONTRACT_VERSION
from nextgen_agent_runtime.orchestration.frappe_client import GatewayError, GatewayRejected

SERVICE_TOKEN = "runtime-dispatch-token"


def make_config(**overrides: Any) -> RuntimeConfig:
    defaults = {
        "runtime_version": "test-runtime",
        "frappe_base_url": "https://erp.internal",
        "frappe_api_key": "key",
        "frappe_api_secret": "secret",
        "service_token": SERVICE_TOKEN,
        "model_base_url": "https://model.internal/v1",
        "model_api_key": "model-key",
        "default_model": "typhoon-test",
        "enabled_agents": frozenset({"sales", "procurement"}),
        # Structured logs are asserted by reading the code, not by scraping test
        # output; keep the suite quiet.
        "log_level": "CRITICAL",
    }
    defaults.update(overrides)
    return RuntimeConfig(**defaults)


class ASGIClient:
    """Drives the ASGI app directly; no socket, no server dependency."""

    def __init__(self, app):
        self.app = app

    def request(self, method: str, path: str, body: Any = None, headers: dict[str, str] | None = None):
        return asyncio.run(self._request(method, path, body, headers))

    async def _request(self, method, path, body, headers):
        raw = b"" if body is None else json.dumps(body).encode()
        header_pairs = [(b"host", b"runtime.internal")]
        for key, value in (headers or {}).items():
            header_pairs.append((key.lower().encode(), value.encode()))
        scope = {
            "type": "http",
            "method": method.upper(),
            "path": path,
            "headers": header_pairs,
        }
        sent: list[dict] = []
        delivered = False

        async def receive():
            nonlocal delivered
            if delivered:
                return {"type": "http.disconnect"}
            delivered = True
            return {"type": "http.request", "body": raw, "more_body": False}

        async def send(message):
            sent.append(message)

        await self.app(scope, receive, send)
        status = next(m["status"] for m in sent if m["type"] == "http.response.start")
        payload = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
        return status, json.loads(payload.decode() or "{}")

    def drain(self):
        asyncio.run(self.app.scheduler.drain())


def run_context(**overrides: Any) -> dict[str, Any]:
    context = {
        "contract_version": CONTRACT_VERSION,
        "run_id": "RUN-0001",
        "correlation_id": "corr-0001",
        "company": "NextGen (Thailand) Co., Ltd.",
        "agent_type": "sales",
        "agent_version": "sales-v1",
        "prompt_version": "sales-prompt-v1",
        "trigger_type": "chat",
        "requested_by": "buyer@nextgen.test",
        "execution_user": "nextgen-agent-runtime@nextgen.test",
        "status": "Running",
        "shadow": False,
        "model": "typhoon-test",
        "max_tool_calls": 4,
        "allowed_tools": ["search_items", "get_item_price_and_stock", "prepare_sales_order"],
        "tool_schemas": [
            _tool_schema("search_items"),
            _tool_schema("get_item_price_and_stock"),
            _tool_schema("prepare_sales_order"),
            # Present in the registry but not allowed for this run.
            _tool_schema("summarize_sales_pipeline"),
        ],
        "input": {"messages": [{"role": "user", "content": "ราคาสินค้า A ตอนนี้เท่าไร"}]},
        "next_sequence": 1,
        "expires_at": None,
    }
    context.update(overrides)
    return context


def _tool_schema(name: str) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": name,
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    }


class FakeGateway:
    """Records every boundary call so tests can assert ordering and keys."""

    def __init__(self, context: dict[str, Any] | None = None, tool_results=None):
        self.context = context or run_context()
        self.tool_results = dict(tool_results or {})
        self.steps: list[dict[str, Any]] = []
        self.tools: list[dict[str, Any]] = []
        self.completed: list[dict[str, Any]] = []
        self.proposals: list[dict[str, Any]] = []
        self.claims = 0
        self.claim_error: Exception | None = None
        self.complete_error: Exception | None = None

    def claim_run(self, run_id: str):
        self.claims += 1
        if self.claim_error:
            raise self.claim_error
        return dict(self.context, run_id=run_id)

    def record_step(self, **kwargs):
        self.steps.append(kwargs)
        return {
            "step_id": f"STEP-{len(self.steps):04d}",
            "sequence": kwargs["sequence"],
            "replayed": False,
        }

    def execute_tool(self, **kwargs):
        self.tools.append(kwargs)
        result = self.tool_results.get(kwargs["tool_name"])
        if isinstance(result, Exception):
            raise result
        return result or {
            "status": "ok",
            "tool_name": kwargs["tool_name"],
            "data": {"ok": True},
            "warnings": [],
            "replayed": False,
        }

    def create_proposal(self, **kwargs):
        self.proposals.append(kwargs)
        return {
            "proposal_id": f"PROP-{len(self.proposals):04d}",
            "status": "Pending Approval",
            "snapshot_hash": "0" * 64,
            "replayed": False,
        }

    def complete_run(self, **kwargs):
        if self.complete_error:
            raise self.complete_error
        self.completed.append(kwargs)


__all__ = [
    "ASGIClient",
    "FakeGateway",
    "GatewayError",
    "GatewayRejected",
    "SERVICE_TOKEN",
    "make_config",
    "run_context",
]
