"""Versioned service contracts shared with the Frappe agent gateway.

This module is the runtime-side copy of the boundary. The Frappe side keeps an
identical copy in ``nextgen_erp.agent_gateway.contracts`` so each service stays
independently deployable. ``contract_fingerprint`` lets the contract tests on
both sides prove the two copies still describe the same payloads without either
service importing the other.

The current version is ``v2``, which adds workflow orchestration. Because the
two services release separately, every v2 addition is optional and ``v1`` stays
accepted for one release.

Payload shapes are deliberately closed (``additionalProperties: false``). An
unknown key is a contract drift, and drift must fail loudly at the boundary
rather than being silently forwarded into ERP.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

CONTRACT_VERSION = "v2"
# v1 stays accepted for one release. The two services deploy independently, so
# a runtime that has not been upgraded yet must keep working against a v2
# gateway; every v2 addition is therefore optional rather than required.
SUPPORTED_CONTRACT_VERSIONS = ("v2", "v1")

TRIGGER_TYPES = ("chat", "manual", "schedule", "webhook", "document_event")
RUN_STATUSES = (
    "Queued",
    "Dispatched",
    "Running",
    "Waiting Approval",
    "Completed",
    "Failed",
    "Cancelled",
)
TERMINAL_RUN_STATUSES = ("Completed", "Failed", "Cancelled")
RUNTIME_COMPLETION_STATUSES = ("Completed", "Failed", "Waiting Approval", "Cancelled")
STEP_TYPES = ("Model", "Tool", "Policy", "Proposal", "System")
STEP_STATUSES = ("Started", "Success", "Error", "Rejected")
PROPOSAL_STATUSES = (
    "Pending Approval",
    "Approved",
    "Rejected",
    "Executing",
    "Completed",
    "Expired",
    "Failed",
    "Superseded",
)
APPROVAL_DECISIONS = ("Approve", "Reject", "Request Changes")
RISK_LEVELS = ("Low", "Medium", "High")
TOOL_RESULT_STATUSES = ("ok", "error", "rejected")

_ID = {"type": "string", "minLength": 1, "maxLength": 140}
_OPTIONAL_ID = {"type": ["string", "null"], "maxLength": 140}
_TEXT = {"type": ["string", "null"], "maxLength": 4000}
_OBJECT = {"type": "object"}
_OPTIONAL_OBJECT = {"type": ["object", "null"]}
_VERSION = {"type": "string", "enum": list(SUPPORTED_CONTRACT_VERSIONS)}


def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


SCHEMAS: dict[str, dict[str, Any]] = {
    # --- Frappe -> runtime -------------------------------------------------
    "dispatch_run_request": _schema(
        {
            "contract_version": _VERSION,
            "run_id": _ID,
            "correlation_id": _ID,
            "runtime_version": _ID,
            "idempotency_key": _ID,
        },
        ["contract_version", "run_id", "correlation_id", "idempotency_key"],
    ),
    "cancel_run_request": _schema(
        {
            "contract_version": _VERSION,
            "run_id": _ID,
            "correlation_id": _OPTIONAL_ID,
            "reason": _TEXT,
        },
        ["contract_version", "run_id"],
    ),
    "accepted": _schema(
        {
            "accepted": {"type": "boolean"},
            "run_id": _ID,
            "status": {"type": "string", "enum": list(RUN_STATUSES)},
        },
        ["accepted", "run_id", "status"],
    ),
    "runtime_health": _schema(
        {
            "status": {"type": "string", "enum": ["ok", "degraded", "starting"]},
            "runtime_version": _ID,
            "contract_version": _VERSION,
            "enabled_agents": {"type": "array", "items": {"type": "string"}},
            "in_flight": {"type": "integer", "minimum": 0},
        },
        ["status", "runtime_version", "contract_version", "enabled_agents", "in_flight"],
    ),
    # --- runtime -> Frappe -------------------------------------------------
    "claim_run_request": _schema(
        {
            "contract_version": _VERSION,
            "run_id": _ID,
            "runtime_version": _ID,
        },
        ["contract_version", "run_id", "runtime_version"],
    ),
    "run_context": _schema(
        {
            "contract_version": _VERSION,
            "run_id": _ID,
            "correlation_id": _ID,
            "company": _ID,
            "agent_type": _ID,
            "agent_version": _ID,
            "prompt_version": _ID,
            "trigger_type": {"type": "string", "enum": list(TRIGGER_TYPES)},
            "requested_by": _ID,
            "execution_user": _ID,
            "status": {"type": "string", "enum": list(RUN_STATUSES)},
            "shadow": {"type": "boolean"},
            "model": _OPTIONAL_ID,
            "max_tool_calls": {"type": "integer", "minimum": 1, "maximum": 12},
            "allowed_tools": {"type": "array", "items": {"type": "string"}},
            # Argument schemas come from Frappe's authoritative tool registry.
            # The runtime narrows this list by name but never adds to it.
            "tool_schemas": {"type": "array", "items": _OBJECT},
            "input": _OBJECT,
            "next_sequence": {"type": "integer", "minimum": 1},
            "expires_at": _OPTIONAL_ID,
            # v2, all optional so a v1 runtime can ignore them. Absent on a
            # plain single-agent run.
            "workflow": _OPTIONAL_ID,
            "workflow_node": _OPTIONAL_ID,
            "parent_run": _OPTIONAL_ID,
            # Present only on the parent run of a workflow: the node and edge
            # graph the executor walks. Each node then gets its own child run.
            "graph": _OPTIONAL_OBJECT,
        },
        [
            "contract_version",
            "run_id",
            "correlation_id",
            "company",
            "agent_type",
            "agent_version",
            "prompt_version",
            "trigger_type",
            "requested_by",
            "execution_user",
            "status",
            "shadow",
            "max_tool_calls",
            "allowed_tools",
            "tool_schemas",
            "input",
            "next_sequence",
        ],
    ),
    "record_step_request": _schema(
        {
            "contract_version": _VERSION,
            "run_id": _ID,
            "sequence": {"type": "integer", "minimum": 1},
            "step_type": {"type": "string", "enum": list(STEP_TYPES)},
            "operation": _ID,
            "status": {"type": "string", "enum": list(STEP_STATUSES)},
            "sanitized_input": _OPTIONAL_OBJECT,
            "result": _OPTIONAL_OBJECT,
            "latency_ms": {"type": ["integer", "null"], "minimum": 0},
            "idempotency_key": _ID,
            "error": _TEXT,
        },
        [
            "contract_version",
            "run_id",
            "sequence",
            "step_type",
            "operation",
            "status",
            "idempotency_key",
        ],
    ),
    "step_reference": _schema(
        {"step_id": _ID, "sequence": {"type": "integer", "minimum": 1}, "replayed": {"type": "boolean"}},
        ["step_id", "sequence", "replayed"],
    ),
    "complete_run_request": _schema(
        {
            "contract_version": _VERSION,
            "run_id": _ID,
            "status": {"type": "string", "enum": list(RUNTIME_COMPLETION_STATUSES)},
            "usage": _OPTIONAL_OBJECT,
            "error": _TEXT,
        },
        ["contract_version", "run_id", "status"],
    ),
    "execute_tool_request": _schema(
        {
            "contract_version": _VERSION,
            "run_id": _ID,
            "sequence": {"type": "integer", "minimum": 1},
            "tool_name": _ID,
            "arguments": _OBJECT,
            "idempotency_key": _ID,
        },
        ["contract_version", "run_id", "sequence", "tool_name", "arguments", "idempotency_key"],
    ),
    "tool_result": _schema(
        {
            "status": {"type": "string", "enum": list(TOOL_RESULT_STATUSES)},
            "tool_name": _ID,
            "data": _OPTIONAL_OBJECT,
            "warnings": {"type": "array", "items": {"type": "string"}},
            "error": _TEXT,
            "replayed": {"type": "boolean"},
        },
        ["status", "tool_name", "warnings", "replayed"],
    ),
    "create_proposal_request": _schema(
        {
            "contract_version": _VERSION,
            "run_id": _ID,
            "action_type": _ID,
            "company": _ID,
            "preview": _OBJECT,
            "policy_snapshot": _OBJECT,
            "risk_level": {"type": "string", "enum": list(RISK_LEVELS)},
            "expires_at": _OPTIONAL_ID,
            "idempotency_key": _ID,
        },
        ["contract_version", "run_id", "action_type", "company", "preview", "idempotency_key"],
    ),
    "proposal_reference": _schema(
        {
            "proposal_id": _ID,
            "status": {"type": "string", "enum": list(PROPOSAL_STATUSES)},
            "snapshot_hash": _ID,
            "expires_at": _OPTIONAL_ID,
            "replayed": {"type": "boolean"},
        },
        ["proposal_id", "status", "snapshot_hash", "replayed"],
    ),
    # v2: the workflow executor asks Frappe to create one child run per node.
    # Frappe still derives company, requester, execution identity and the tool
    # allowlist from the parent run; the runtime supplies only the node.
    "create_node_run_request": _schema(
        {
            "contract_version": _VERSION,
            "parent_run_id": _ID,
            "node_id": _ID,
            "agent_type": _ID,
            "node_prompt": _TEXT,
            "input": _OBJECT,
            "idempotency_key": _ID,
        },
        ["contract_version", "parent_run_id", "node_id", "agent_type", "input", "idempotency_key"],
    ),
}


class ContractError(ValueError):
    """Raised when a payload does not match its versioned schema."""


def _type_matches(value: Any, expected: str) -> bool:
    if expected == "null":
        return value is None
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "string":
        return isinstance(value, str)
    if expected == "object":
        return isinstance(value, dict)
    if expected == "array":
        return isinstance(value, list)
    raise ContractError(f"unsupported schema type: {expected}")


def _validate(value: Any, schema: dict[str, Any], path: str) -> None:
    expected = schema.get("type")
    if expected is not None:
        options = expected if isinstance(expected, list) else [expected]
        if not any(_type_matches(value, option) for option in options):
            raise ContractError(f"{path}: expected {'/'.join(options)}")
    if value is None:
        return
    if "enum" in schema and value not in schema["enum"]:
        raise ContractError(f"{path}: {value!r} is not an allowed value")
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            raise ContractError(f"{path}: shorter than {schema['minLength']} characters")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            raise ContractError(f"{path}: longer than {schema['maxLength']} characters")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            raise ContractError(f"{path}: below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            raise ContractError(f"{path}: above maximum {schema['maximum']}")
    if isinstance(value, list):
        item_schema = schema.get("items")
        if item_schema:
            for index, item in enumerate(value):
                _validate(item, item_schema, f"{path}[{index}]")
    if isinstance(value, dict):
        properties = schema.get("properties") or {}
        for key in schema.get("required") or []:
            if key not in value:
                raise ContractError(f"{path}: missing required field {key!r}")
        if properties and schema.get("additionalProperties") is False:
            unknown = sorted(set(value) - set(properties))
            if unknown:
                raise ContractError(f"{path}: unknown field(s) {', '.join(unknown)}")
        for key, item_schema in properties.items():
            if key in value:
                _validate(value[key], item_schema, f"{path}.{key}")


def validate(schema_name: str, payload: Any) -> dict[str, Any]:
    """Validate ``payload`` against a named ``v1`` schema and return it."""
    schema = SCHEMAS.get(schema_name)
    if schema is None:
        raise ContractError(f"unknown contract schema: {schema_name}")
    _validate(payload, schema, schema_name)
    return payload


def canonical_json(payload: Any) -> str:
    """Stable JSON used for hashes on both sides of the boundary."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def payload_hash(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def contract_fingerprint() -> str:
    """Hash of the whole ``v1`` surface, compared by both services' tests."""
    return payload_hash({"version": CONTRACT_VERSION, "schemas": SCHEMAS})
