"""The tool loop.

The runner owns orchestration and nothing else. It selects no quantities,
resolves no permissions and writes no ERP data: it asks Frappe to run
allowlisted domain tools and records what happened. Every exit path is
fail-closed — a model, network or gateway failure ends as a Failed run with a
sanitised error, never as a silent success or an unrecorded side effect.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .. import telemetry
from ..agents import DEFINITIONS, AgentDefinition, UnknownAgentError, get_definition
from ..models.contracts import TERMINAL_RUN_STATUSES
from ..models.provider import ModelError, ModelResponse
from ..redaction import redact, redact_error
from .frappe_client import GatewayError, GatewayRejected

MAX_TOOL_RESULT_CHARS = 6000
CONTENT_PREVIEW_CHARS = 400


class RunRejected(RuntimeError):
    """The run cannot be executed by this deployment. Recorded, never retried."""


@dataclass
class RunOutcome:
    run_id: str
    status: str
    steps_recorded: int = 0
    tool_calls: int = 0
    proposals: list[str] = field(default_factory=list)
    error: str = ""
    #: The turn's final answer, sanitized. A workflow feeds this to the next node.
    final_text: str = ""


class RunExecutor:
    def __init__(
        self,
        config,
        gateway,
        provider,
        definitions: dict[str, AgentDefinition] | None = None,
        cancel_check: Callable[[str], bool] | None = None,
    ):
        self.config = config
        self.gateway = gateway
        self.provider = provider
        self.definitions = definitions if definitions is not None else DEFINITIONS
        # Cancellation is cooperative and checked between turns. A run already
        # inside a tool call finishes it; Frappe's Cancelled state is what
        # actually stops further effects.
        self.cancel_check = cancel_check

    # -- public ------------------------------------------------------------

    def execute(self, run_id: str) -> RunOutcome:
        """Claim and execute one run. Never raises; the outcome is the record."""
        outcome = RunOutcome(run_id=run_id, status="Failed")
        try:
            context = self.gateway.claim_run(run_id)
        except GatewayError as exc:
            # Claiming failed, so Frappe still owns the run state. Leave it
            # Queued or Dispatched for the dispatcher to retry.
            telemetry.error("run.claim_failed", run_id=run_id, error=redact_error(exc))
            outcome.error = redact_error(exc)
            outcome.status = "Unclaimed"
            return outcome

        correlation_id = context["correlation_id"]
        with telemetry.bind(
            correlation_id=correlation_id,
            run_id=run_id,
            company=context["company"],
            agent_type=context["agent_type"],
            runtime_version=self.config.runtime_version,
        ):
            if context["status"] in TERMINAL_RUN_STATUSES:
                telemetry.info("run.already_final", status=context["status"])
                outcome.status = context["status"]
                return outcome
            if context.get("graph"):
                # A parent workflow run: orchestrate the graph instead of
                # running a single agent turn.
                return self._run_workflow(context, outcome)
            try:
                return self._run(context, outcome)
            except RunRejected as exc:
                return self._fail(context, outcome, exc, level="rejected")
            except (GatewayRejected, GatewayError, ModelError) as exc:
                return self._fail(context, outcome, exc)
            except Exception as exc:  # pragma: no cover - defensive fail-closed
                telemetry.error("run.unexpected_error", error=redact_error(exc))
                return self._fail(context, outcome, exc)

    # -- internals ---------------------------------------------------------

    def _run_workflow(self, context: dict[str, Any], outcome: RunOutcome) -> RunOutcome:
        """Hand a parent workflow run to the graph executor.

        Imported here rather than at module scope because the workflow executor
        composes this class.
        """
        from .workflow import WorkflowExecutor

        executor = WorkflowExecutor(self.config, self.gateway, self.provider, node_executor=self)
        result = executor.execute(context)
        outcome.status = result.status
        outcome.proposals = list(result.proposals)
        outcome.error = result.error
        return outcome

    def _fail(self, context: dict[str, Any], outcome: RunOutcome, exc: BaseException, *, level="error"):
        message = redact_error(exc)
        outcome.status = "Failed"
        outcome.error = message
        telemetry.error("run.failed", reason=level, error=message)
        try:
            self.gateway.complete_run(
                run_id=outcome.run_id,
                status="Failed",
                error=message,
                correlation_id=context.get("correlation_id"),
            )
        except GatewayError as complete_error:
            # Frappe still holds the run; its stale-run reaper resolves it.
            telemetry.error("run.complete_failed", error=redact_error(complete_error))
        return outcome

    def _resolve_definition(self, context: dict[str, Any]) -> AgentDefinition:
        agent_type = context["agent_type"]
        if agent_type not in self.config.enabled_agents:
            raise RunRejected(f"agent {agent_type!r} is not enabled in this deployment")
        try:
            definition = get_definition(agent_type)
        except UnknownAgentError as exc:
            raise RunRejected(str(exc))
        if definition.key not in self.definitions:
            raise RunRejected(f"agent {agent_type!r} is not available in this release")
        return definition

    def _run(self, context: dict[str, Any], outcome: RunOutcome) -> RunOutcome:
        definition = self._resolve_definition(context)
        allowed = definition.effective_tools(context["allowed_tools"])
        schemas = _filter_schemas(context["tool_schemas"], allowed)
        sequence = int(context["next_sequence"])
        correlation_id = context["correlation_id"]
        model = context.get("model") or self.config.default_model
        if not model:
            raise RunRejected("no model configured for this run")

        sequence = self._record(
            context,
            outcome,
            sequence,
            step_type="System",
            operation="run.claimed",
            status="Success",
            sanitized_input={
                "agent_version": definition.version,
                "prompt_version": definition.prompt_version,
                "allowed_tools": list(allowed),
                "shadow": bool(context["shadow"]),
                "model": model,
            },
        )

        messages = self._build_messages(definition, context)
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
        budget = min(int(context["max_tool_calls"]), definition.max_tool_calls)
        final_text = ""

        for _turn in range(budget):
            if self.cancel_check and self.cancel_check(outcome.run_id):
                self.gateway.complete_run(
                    run_id=outcome.run_id,
                    status="Cancelled",
                    error="cancelled before the next model turn",
                    correlation_id=correlation_id,
                )
                outcome.status = "Cancelled"
                telemetry.info("run.cancelled")
                return outcome
            started = time.monotonic()
            response = self.provider.complete(model=model, messages=messages, tools=schemas)
            latency_ms = int((time.monotonic() - started) * 1000)
            for key in usage:
                usage[key] += int(response.usage.get(key) or 0)
            sequence = self._record(
                context,
                outcome,
                sequence,
                step_type="Model",
                operation="model.completion",
                status="Success",
                latency_ms=latency_ms,
                sanitized_input={
                    "model": model,
                    "prompt_version": definition.prompt_version,
                    "message_count": len(messages),
                    "tool_count": len(schemas),
                },
                # Bounded metadata only. Hidden model reasoning is never sent
                # to Frappe and never persisted.
                result={
                    "content_preview": response.content[:CONTENT_PREVIEW_CHARS],
                    "tool_calls": [call.name for call in response.tool_calls],
                    "finish_reason": response.finish_reason,
                    "usage": response.usage,
                },
            )

            if not response.tool_calls:
                final_text = response.content
                break

            messages.append(response.as_assistant_message())
            for call in response.tool_calls:
                sequence, tool_message = self._invoke_tool(
                    context, outcome, sequence, definition, allowed, call
                )
                messages.append(tool_message)
        else:
            telemetry.warning("run.tool_budget_exhausted", budget=budget)

        status = "Waiting Approval" if outcome.proposals else "Completed"
        outcome.final_text = redact(final_text)[:CONTENT_PREVIEW_CHARS]
        self.gateway.complete_run(
            run_id=outcome.run_id,
            status=status,
            usage={
                **usage,
                "tool_calls": outcome.tool_calls,
                "steps": outcome.steps_recorded,
                "final_text": redact(final_text)[:CONTENT_PREVIEW_CHARS],
                "proposals": outcome.proposals,
            },
            correlation_id=correlation_id,
        )
        outcome.status = status
        telemetry.info("run.completed", status=status, tool_calls=outcome.tool_calls)
        return outcome

    def _invoke_tool(self, context, outcome, sequence, definition, allowed, call):
        """Dispatch one model-selected tool call and return its tool message."""
        if call.name not in allowed:
            # A model may not widen its own allowlist. Frappe would reject this
            # too; recording it here keeps the refusal in the ordered trace.
            sequence = self._record(
                context,
                outcome,
                sequence,
                step_type="Tool",
                operation=call.name,
                status="Rejected",
                sanitized_input={"tool_name": call.name},
                error=f"tool {call.name!r} is not allowed for agent {definition.key!r}",
            )
            return sequence, _tool_message(
                call.id, {"error": f"Tool not allowed for agent {definition.key}: {call.name}"}
            )

        current = sequence
        sequence += 1
        try:
            result = self.gateway.execute_tool(
                run_id=outcome.run_id,
                sequence=current,
                tool_name=call.name,
                arguments=call.arguments,
                idempotency_key=_key(outcome.run_id, current, call.name),
                correlation_id=context["correlation_id"],
            )
        except GatewayRejected as exc:
            # Frappe declined: permission, policy, company or contract. The
            # authoritative Tool step is already recorded on its side.
            outcome.tool_calls += 1
            telemetry.warning("tool.rejected", tool=call.name, error=redact_error(exc))
            return sequence, _tool_message(call.id, {"error": redact_error(exc)})

        outcome.tool_calls += 1
        outcome.steps_recorded += 1
        data = result.get("data") or {}
        proposal_id = data.get("proposal_id") if isinstance(data, dict) else None
        if proposal_id:
            outcome.proposals.append(str(proposal_id))
            telemetry.info("proposal.created", proposal_id=str(proposal_id), tool=call.name)
        return sequence, _tool_message(call.id, result)

    def _record(
        self,
        context,
        outcome,
        sequence: int,
        *,
        step_type: str,
        operation: str,
        status: str,
        sanitized_input: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        latency_ms: int | None = None,
        error: str | None = None,
    ) -> int:
        self.gateway.record_step(
            run_id=outcome.run_id,
            sequence=sequence,
            step_type=step_type,
            operation=operation,
            status=status,
            idempotency_key=_key(outcome.run_id, sequence, operation),
            sanitized_input=sanitized_input,
            result=result,
            latency_ms=latency_ms,
            error=error,
            correlation_id=context["correlation_id"],
        )
        outcome.steps_recorded += 1
        return sequence + 1

    def _build_messages(self, definition: AgentDefinition, context: dict[str, Any]):
        payload = context.get("input") or {}
        page_context = payload.get("context") or {}
        system = definition.system_prompt
        if page_context:
            system = f"{system}\nบริบทหน้า ERP ปัจจุบัน: {json.dumps(page_context, ensure_ascii=False)}"
        messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
        for turn in payload.get("messages") or []:
            if not isinstance(turn, dict):
                continue
            role = str(turn.get("role") or "")
            if role not in ("user", "assistant"):
                continue
            messages.append({"role": role, "content": str(turn.get("content") or "")})
        return messages


def _filter_schemas(schemas: list[dict[str, Any]], allowed: tuple[str, ...]) -> list[dict[str, Any]]:
    names = set(allowed)
    return [
        schema
        for schema in schemas
        if isinstance(schema, dict) and ((schema.get("function") or {}).get("name")) in names
    ]


def _key(run_id: str, sequence: int, operation: str) -> str:
    """Deterministic so a restart replaying the same step reuses its key."""
    return f"{run_id}:{sequence}:{operation}"[:140]


def _tool_message(tool_call_id: str, payload: Any) -> dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": tool_call_id,
        "content": json.dumps(payload, ensure_ascii=False, default=str)[:MAX_TOOL_RESULT_CHARS],
    }


def build_response(content: str, tool_calls=()) -> ModelResponse:  # pragma: no cover - test helper
    return ModelResponse(content=content, tool_calls=tuple(tool_calls))
