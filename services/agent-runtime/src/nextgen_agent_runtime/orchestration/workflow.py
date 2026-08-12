"""Multi-agent workflow execution.

A workflow run walks a validated graph in topological order and executes each
node as an ordinary agent turn, feeding the previous node's answer forward. It
adds orchestration and nothing else: every node still goes through
:class:`RunExecutor`, so the tool allowlist, company scoping, proposals,
approval and audit are identical to a single-agent run.

Two properties matter most:

* **A workflow cannot approve its own work.** If any node produces a proposal,
  the parent run ends at ``Waiting Approval`` and the remaining nodes do not
  run. A human decides before anything else happens.
* **A restart replays rather than duplicates.** Node run keys are derived from
  the parent run and node id, so re-dispatching the same workflow run reuses
  the child runs it already created.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .. import telemetry
from ..models.contracts import TERMINAL_RUN_STATUSES
from ..redaction import redact_error
from .frappe_client import GatewayError, GatewayRejected
from .runner import RunExecutor, RunRejected

MAX_NODES = 40
CARRIED_ANSWER_CHARS = 4000


class GraphError(ValueError):
    """The graph is unusable: cyclic, empty, or structurally broken."""


@dataclass
class WorkflowOutcome:
    run_id: str
    status: str
    nodes_run: list[str] = field(default_factory=list)
    proposals: list[str] = field(default_factory=list)
    error: str = ""


def node_agent(node: dict[str, Any]) -> str:
    data = node.get("data") if isinstance(node.get("data"), dict) else {}
    return str(data.get("agent") or "").strip()


def node_prompt(node: dict[str, Any]) -> str:
    data = node.get("data") if isinstance(node.get("data"), dict) else {}
    return str(data.get("prompt") or "").strip()


def topological_order(graph: dict[str, Any]) -> list[dict[str, Any]]:
    """Kahn's algorithm, tie-broken by canvas position then id.

    Frappe validates the graph on save, so this should never fail in practice.
    It is re-checked because executing a cycle would loop forever.
    """
    nodes = {str(n["id"]): n for n in (graph.get("nodes") or []) if isinstance(n, dict) and n.get("id")}
    if not nodes:
        raise GraphError("workflow graph has no nodes")
    if len(nodes) > MAX_NODES:
        raise GraphError(f"workflow graph exceeds {MAX_NODES} nodes")

    incoming = {node_id: 0 for node_id in nodes}
    outgoing: dict[str, list[str]] = {node_id: [] for node_id in nodes}
    for edge in graph.get("edges") or []:
        if not isinstance(edge, dict):
            continue
        source, target = str(edge.get("source") or ""), str(edge.get("target") or "")
        if source in nodes and target in nodes:
            outgoing[source].append(target)
            incoming[target] += 1

    def sort_key(node_id: str) -> tuple:
        position = nodes[node_id].get("position") or {}
        return (float(position.get("x") or 0), float(position.get("y") or 0), node_id)

    ready = sorted([n for n, count in incoming.items() if count == 0], key=sort_key)
    ordered: list[dict[str, Any]] = []
    while ready:
        node_id = ready.pop(0)
        ordered.append(nodes[node_id])
        for target in outgoing[node_id]:
            incoming[target] -= 1
            if incoming[target] == 0:
                ready.append(target)
        ready.sort(key=sort_key)

    if len(ordered) != len(nodes):
        raise GraphError("workflow graph contains a cycle")
    return ordered


class WorkflowExecutor:
    """Runs one workflow: a parent run plus one child run per node."""

    def __init__(self, config, gateway, provider, node_executor: RunExecutor | None = None):
        self.config = config
        self.gateway = gateway
        self.provider = provider
        # Nodes reuse the ordinary single-agent executor unchanged.
        self.node_executor = node_executor or RunExecutor(config, gateway, provider)

    def execute(self, parent_context: dict[str, Any]) -> WorkflowOutcome:
        run_id = parent_context["run_id"]
        outcome = WorkflowOutcome(run_id=run_id, status="Failed")
        correlation_id = parent_context["correlation_id"]
        try:
            nodes = topological_order(parent_context.get("graph") or {})
        except GraphError as exc:
            return self._fail(parent_context, outcome, exc)

        telemetry.info("workflow.started", nodes=len(nodes), workflow=parent_context.get("workflow"))
        carried = str((parent_context.get("input") or {}).get("answer") or "")
        seed = _seed_message(parent_context)

        for index, node in enumerate(nodes):
            node_id = str(node["id"])
            try:
                child = self.gateway.create_node_run(
                    parent_run_id=run_id,
                    node_id=node_id,
                    agent_type=node_agent(node),
                    node_prompt=node_prompt(node),
                    input_payload=_node_input(node, seed, carried),
                    # Deterministic: re-dispatching the parent reuses this run.
                    idempotency_key=f"{run_id}:node:{node_id}",
                    correlation_id=correlation_id,
                )
            except (GatewayRejected, GatewayError) as exc:
                return self._fail(parent_context, outcome, exc)

            if child["status"] in TERMINAL_RUN_STATUSES:
                # Already executed on a previous attempt; carry its result on.
                telemetry.info("workflow.node_replayed", node=node_id, status=child["status"])
                outcome.nodes_run.append(node_id)
                continue

            node_outcome = self.node_executor.execute(child["run_id"])
            outcome.nodes_run.append(node_id)
            if node_outcome.status not in ("Completed", "Waiting Approval"):
                return self._fail(
                    parent_context,
                    outcome,
                    RunRejected(f"node {node_id!r} ended as {node_outcome.status}"),
                )
            if node_outcome.proposals:
                # A human decides before the rest of the graph runs.
                outcome.proposals.extend(node_outcome.proposals)
                telemetry.info("workflow.waiting_approval", node=node_id, remaining=len(nodes) - index - 1)
                return self._complete(parent_context, outcome, "Waiting Approval")
            carried = _carry(node_outcome)

        return self._complete(parent_context, outcome, "Completed")

    # -- internals ---------------------------------------------------------

    def _complete(self, context, outcome: WorkflowOutcome, status: str) -> WorkflowOutcome:
        outcome.status = status
        try:
            self.gateway.complete_run(
                run_id=outcome.run_id,
                status=status,
                usage={
                    "nodes_run": outcome.nodes_run,
                    "proposals": outcome.proposals,
                    "workflow": context.get("workflow"),
                },
                correlation_id=context.get("correlation_id"),
            )
        except GatewayError as exc:
            telemetry.error("workflow.complete_failed", error=redact_error(exc))
        telemetry.info("workflow.finished", status=status, nodes=len(outcome.nodes_run))
        return outcome

    def _fail(self, context, outcome: WorkflowOutcome, exc: BaseException) -> WorkflowOutcome:
        message = redact_error(exc)
        outcome.status = "Failed"
        outcome.error = message
        telemetry.error("workflow.failed", error=message)
        try:
            self.gateway.complete_run(
                run_id=outcome.run_id,
                status="Failed",
                error=message,
                correlation_id=context.get("correlation_id"),
            )
        except GatewayError as complete_error:
            telemetry.error("workflow.complete_failed", error=redact_error(complete_error))
        return outcome


def _seed_message(context: dict[str, Any]) -> str:
    for turn in (context.get("input") or {}).get("messages") or []:
        if isinstance(turn, dict) and turn.get("role") == "user":
            return str(turn.get("content") or "")
    return ""


def _node_input(node: dict[str, Any], seed: str, carried: str) -> dict[str, Any]:
    """What this node sees: the original request plus the previous answer."""
    messages = []
    if seed:
        messages.append({"role": "user", "content": seed})
    if carried:
        messages.append(
            {"role": "user", "content": f"ผลลัพธ์จากขั้นตอนก่อนหน้า:\n{carried[:CARRIED_ANSWER_CHARS]}"}
        )
    return {"messages": messages, "context": {"node": str(node.get("id") or "")}}


def _carry(node_outcome) -> str:
    """The previous node's sanitized answer, handed to the next node."""
    return getattr(node_outcome, "final_text", "") or ""
