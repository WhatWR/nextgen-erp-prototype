"""OpenAPI document generated from the versioned ``v1`` contracts.

The published document is derived from the same schema objects the request
handlers validate against, so the specification cannot drift away from the code
that enforces it.
"""

from __future__ import annotations

from typing import Any

from ..models import contracts

SECURITY_SCHEME = "ServiceToken"

_OPERATIONS: list[dict[str, Any]] = [
    {
        "path": "/healthz",
        "method": "get",
        "operation_id": "get_runtime_health",
        "summary": "Liveness and runtime metadata",
        "response": "runtime_health",
        "public": True,
    },
    {
        "path": "/readyz",
        "method": "get",
        "operation_id": "get_runtime_readiness",
        "summary": "Readiness; 503 until gateway, dispatch and model credentials are configured",
        "response": "runtime_health",
        "public": True,
    },
    {
        "path": "/v1/runs/dispatch",
        "method": "post",
        "operation_id": "dispatch_run",
        "summary": "Accept a queued Agent Run for asynchronous execution",
        "request": "dispatch_run_request",
        "response": "accepted",
        "status": "202",
    },
    {
        "path": "/v1/runs/resume",
        "method": "post",
        "operation_id": "resume_run",
        "summary": "Re-claim a persisted run and continue from its next sequence",
        "request": "dispatch_run_request",
        "response": "accepted",
        "status": "202",
    },
    {
        "path": "/v1/runs/cancel",
        "method": "post",
        "operation_id": "cancel_run",
        "summary": "Request cooperative cancellation of a run",
        "request": "cancel_run_request",
        "response": "accepted",
        "status": "202",
    },
]


def _json_content(schema_name: str) -> dict[str, Any]:
    return {
        "content": {"application/json": {"schema": {"$ref": f"#/components/schemas/{schema_name}"}}}
    }


def build_openapi(runtime_version: str) -> dict[str, Any]:
    paths: dict[str, Any] = {}
    for operation in _OPERATIONS:
        entry: dict[str, Any] = {
            "operationId": operation["operation_id"],
            "summary": operation["summary"],
            "responses": {
                operation.get("status", "200"): {
                    "description": "Success",
                    **_json_content(operation["response"]),
                },
                "401": {"description": "Missing or invalid service token"},
                "400": {"description": "Contract violation"},
            },
        }
        if operation.get("request"):
            entry["requestBody"] = {"required": True, **_json_content(operation["request"])}
        if not operation.get("public"):
            entry["security"] = [{SECURITY_SCHEME: []}]
        paths.setdefault(operation["path"], {})[operation["method"]] = entry

    return {
        "openapi": "3.1.0",
        "info": {
            "title": "NextGen Agent Runtime",
            "version": runtime_version,
            "description": (
                "Orchestration microservice for NextGen ERP. Reached only by the Frappe "
                "agent gateway over a private service network; it owns no ERP data."
            ),
            "x-contract-version": contracts.CONTRACT_VERSION,
            "x-contract-fingerprint": contracts.contract_fingerprint(),
        },
        "components": {
            "schemas": dict(contracts.SCHEMAS),
            "securitySchemes": {
                SECURITY_SCHEME: {
                    "type": "apiKey",
                    "in": "header",
                    "name": "X-NextGen-Service-Token",
                }
            },
        },
        "paths": paths,
    }
